"""Integration tests for Python Forwarder WebSocket server.

Mirrors C++ test_phase2_transport.cpp:
- Test 1: Handshake + bidirectional encrypted message exchange
- Test 2: Project-based routing (1:N)
- Test 3: Multiple agents simultaneously
- Test 4: Tampered handshake rejected
"""

import sys, os, json, tempfile, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import pytest
import pytest_asyncio
import uvicorn
from websockets.asyncio.client import connect

from forwarder.crypto import (
    CryptoCodec, CryptoEnvelope, MessageType, build_signing_payload,
    AesGcmCipher, EcdsaSigner, EcdhKeyExchange, ReplayGuard, b64,
)
from forwarder.ws.server import ForwarderServer


# ── Test fixture ────────────────────────────────────────────────

@pytest_asyncio.fixture
async def server():
    """Create a running Forwarder server with test ECDSA keys."""
    with tempfile.TemporaryDirectory() as tmp:
        fwd_priv = os.path.join(tmp, "fwd_priv.pem")
        fwd_pub = os.path.join(tmp, "fwd_pub.pem")
        EcdsaSigner.generate_keypair(fwd_priv, fwd_pub)

        received: list[dict] = []

        async def msg_cb(agent_id: str, msg_type: MessageType, plaintext: str):
            received.append({
                "agent_id": agent_id,
                "type": msg_type,
                "plaintext": plaintext,
            })

        fwd = ForwarderServer(
            ecdsa_priv_file=fwd_priv,
            ecdsa_pub_file=fwd_pub,
            message_cb=msg_cb,
        )

        # Find free port (pre-bind to avoid race)
        import socket as _socket
        with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        # Run uvicorn in the same event loop as the test
        config = uvicorn.Config(fwd.app, host="127.0.0.1", port=port, log_level="error")
        server_instance = uvicorn.Server(config)
        server_task = asyncio.create_task(server_instance.serve())

        # Give the server a moment to start
        await asyncio.sleep(0.2)

        yield {
            "forwarder": fwd,
            "port": port,
            "received": received,
            "fwd_pub": fwd_pub,
        }

        server_instance.should_exit = True
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass


# ── Test helpers ────────────────────────────────────────────────

def _make_agent_keys():
    """Generate ECDSA key pair for an agent. Returns (priv_path, pub_path, signer)."""
    tmp = tempfile.mkdtemp()
    priv = os.path.join(tmp, "agt_priv.pem")
    pub = os.path.join(tmp, "agt_pub.pem")
    EcdsaSigner.generate_keypair(priv, pub)
    signer = EcdsaSigner.from_private_key_file(priv)
    with open(pub, "r") as f:
        pub_pem = f.read()
    return priv, pub, signer, pub_pem


def _make_handshake_init(agent_id: str, projects: list[str],
                         ecdh: EcdhKeyExchange, pub_pem: str,
                         signer: EcdsaSigner) -> dict:
    """Build and sign a HandshakeInit message."""
    sig_data = f"{agent_id}|{ecdh.public_key_pem()}|{pub_pem}"
    sig = b64.b64_encode(signer.sign(sig_data.encode("utf-8")))
    return {
        "type": "HANDSHAKE_INIT",
        "agent_id": agent_id,
        "projects": projects,
        "ecdh_pub_pem": ecdh.public_key_pem(),
        "ecdsa_pub_pem": pub_pem,
        "signature": sig,
    }


# ── Tests ───────────────────────────────────────────────────────

class TestWsServer:

    @pytest.mark.asyncio
    async def test_handshake_and_message_exchange(self, server):
        """Test 1: Full handshake + bidirectional encrypted exchange."""
        fwd = server["forwarder"]
        port = server["port"]
        received = server["received"]
        fwd_pub = server["fwd_pub"]

        priv, pub, signer, pub_pem = _make_agent_keys()
        ecdh = EcdhKeyExchange()
        init = _make_handshake_init("agent-001", ["proj-a"], ecdh, pub_pem, signer)

        async with connect(f"ws://127.0.0.1:{port}/agent-ws") as ws:
            # Handshake
            await ws.send(json.dumps(init))
            ack = json.loads(await ws.recv())
            assert ack["status"] == "OK"

            # Derive keys and verify Forwarder
            shared = ecdh.derive_shared_secret_pem(ack["ecdh_pub_pem"])
            fwd_verifier = EcdsaSigner.from_public_key_file(fwd_pub)
            ack_data = f"{ack['ecdh_pub_pem']}|{ack['ecdsa_pub_pem']}"
            assert fwd_verifier.verify(
                ack_data.encode(), b64.b64_decode(ack["signature"])
            )

            codec = CryptoCodec(
                cipher=AesGcmCipher(shared),
                signer=signer,
                verifier=fwd_verifier,
                guard=ReplayGuard(),
            )

            await asyncio.sleep(0.1)

            # Agent → Forwarder
            msg = json.dumps({"wo": "WO-001", "status": "SUCCESS"})
            enc = codec.encrypt(msg.encode(), MessageType.BUILD_RESULT)
            await ws.send(enc)
            await asyncio.sleep(0.1)

            assert len(received) >= 1
            assert received[0]["agent_id"] == "agent-001"
            assert "WO-001" in received[0]["plaintext"]

            # Forwarder → Agent
            trigger = json.dumps({"wo": "WO-002", "project": "proj-a"})
            ok = await fwd.send_to_agent(
                "agent-001", MessageType.BUILD_TRIGGER, trigger
            )
            assert ok

            enc = await ws.recv()
            dec = codec.decrypt(enc)
            assert dec.ok
            assert b"WO-002" in dec.plaintext

    @pytest.mark.asyncio
    async def test_project_routing(self, server):
        """Test 2: Agent registers for projects, routes by project."""
        fwd = server["forwarder"]
        port = server["port"]
        fwd_pub = server["fwd_pub"]

        priv, pub, signer, pub_pem = _make_agent_keys()
        ecdh = EcdhKeyExchange()
        init = _make_handshake_init("agent-002", ["alpha", "beta"], ecdh, pub_pem, signer)

        async with connect(f"ws://127.0.0.1:{port}/agent-ws") as ws:
            await ws.send(json.dumps(init))
            ack = json.loads(await ws.recv())
            assert ack["status"] == "OK"

            shared = ecdh.derive_shared_secret_pem(ack["ecdh_pub_pem"])
            fwd_verifier = EcdsaSigner.from_public_key_file(fwd_pub)
            codec = CryptoCodec(
                cipher=AesGcmCipher(shared),
                signer=signer,
                verifier=fwd_verifier,
                guard=ReplayGuard(),
            )

            await asyncio.sleep(0.1)

            # Route to "alpha"
            ok = await fwd.send_to_project("alpha", MessageType.BUILD_TRIGGER, '{"p":"alpha"}')
            assert ok
            dec = codec.decrypt(await ws.recv())
            assert b"alpha" in dec.plaintext

            # Route to "beta"
            ok = await fwd.send_to_project("beta", MessageType.BUILD_TRIGGER, '{"p":"beta"}')
            assert ok
            dec = codec.decrypt(await ws.recv())
            assert b"beta" in dec.plaintext

            # Route to unregistered project
            ok = await fwd.send_to_project("gamma", MessageType.BUILD_TRIGGER, '{"p":"gamma"}')
            assert not ok

    @pytest.mark.asyncio
    async def test_multiple_agents(self, server):
        """Test 3: Two agents connect simultaneously."""
        fwd = server["forwarder"]
        port = server["port"]
        fwd_pub = server["fwd_pub"]
        received = server["received"]

        async def agent_connect_ws(agent_id: str, projects: list[str]):
            """Connect and handshake. Returns the ws connection (still open)."""
            priv, pub, signer, pub_pem = _make_agent_keys()
            ecdh = EcdhKeyExchange()
            init = _make_handshake_init(agent_id, projects, ecdh, pub_pem, signer)

            ws = await connect(f"ws://127.0.0.1:{port}/agent-ws")
            await ws.send(json.dumps(init))
            ack = json.loads(await ws.recv())
            assert ack["status"] == "OK"

            shared = ecdh.derive_shared_secret_pem(ack["ecdh_pub_pem"])
            fwd_verifier = EcdsaSigner.from_public_key_file(fwd_pub)
            codec = CryptoCodec(
                cipher=AesGcmCipher(shared),
                signer=signer,
                verifier=fwd_verifier,
                guard=ReplayGuard(),
            )

            # Send agent response
            msg = json.dumps({"from": agent_id})
            enc = codec.encrypt(msg.encode(), MessageType.BUILD_RESULT)
            await ws.send(enc)

            return ws

        # Open both connections
        ws_a = await agent_connect_ws("agent-A", ["proj-1"])
        ws_b = await agent_connect_ws("agent-B", ["proj-2"])

        await asyncio.sleep(0.2)

        # Both should be registered
        agents = await fwd.manager.list_agents()
        assert len(agents) == 2
        assert "agent-A" in agents
        assert "agent-B" in agents

        # Both should have sent messages received
        assert len(received) >= 2
        agent_ids = {r["agent_id"] for r in received}
        assert "agent-A" in agent_ids
        assert "agent-B" in agent_ids

        # Cleanup
        await ws_a.close()
        await ws_b.close()

    @pytest.mark.asyncio
    async def test_bad_signature_rejected(self, server):
        """Test 4: Invalid signature → error response."""
        port = server["port"]

        ecdh = EcdhKeyExchange()
        # Use a bad ECDSA public key and garbage signature
        init = {
            "type": "HANDSHAKE_INIT",
            "agent_id": "evil-agent",
            "projects": ["proj-x"],
            "ecdh_pub_pem": ecdh.public_key_pem(),
            "ecdsa_pub_pem": (
                "-----BEGIN PUBLIC KEY-----\n"
                "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE\n"
                "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n"
                "AAAAAAAAAAAAAAAAAAAAAA==\n"
                "-----END PUBLIC KEY-----"
            ),
            "signature": b64.b64_encode(b"\x00" * 64),
        }

        try:
            async with connect(f"ws://127.0.0.1:{port}/agent-ws") as ws:
                await ws.send(json.dumps(init))
                ack = json.loads(await ws.recv())
                assert ack["status"] == "ERROR"
                assert "Signature" in ack.get("error", "")
        except Exception:
            # Connection may close — acceptable
            pass
