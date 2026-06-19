"""AgentHandler — handles one Agent WebSocket connection.

Matches C++ AgentSession flow:
1. Receive HandshakeInit (plaintext JSON)
2. Verify ECDSA signature
3. Generate ECDH key pair, derive AES session key
4. Send HandshakeAck (signed)
5. Register with AgentManager
6. Encrypted I/O loop: receive CryptoEnvelope, decrypt, dispatch
7. Unregister on disconnect
"""

import asyncio
import json
import logging
from typing import Callable, Awaitable

from starlette.websockets import WebSocket, WebSocketDisconnect

from ..crypto import (
    CryptoCodec, CryptoEnvelope, MessageType,
    AesGcmCipher, EcdsaSigner, EcdhKeyExchange, ReplayGuard, b64,
)
from .agent_manager import AgentManager

logger = logging.getLogger(__name__)

MessageCallback = Callable[[str, MessageType, str], Awaitable[None]]
ConnectCallback = Callable[[str, bool], Awaitable[None]]


class HandshakeError(Exception):
    pass


# ── Public API ──────────────────────────────────────────────────

async def handle_agent_connection(
    websocket: WebSocket,
    manager: AgentManager,
    ecdsa_priv_file: str,
    ecdsa_pub_file: str,
    message_cb: MessageCallback | None = None,
    connect_cb: ConnectCallback | None = None,
) -> None:
    """Handle one Agent WebSocket connection from accept to disconnect.

    Lifecycle (matches C++ AgentSession):
    1. WebSocket accept
    2. Handshake (ECDH + ECDSA)
    3. Register with AgentManager
    4. Encrypted I/O loop
    5. Unregister on disconnect/error
    """
    await websocket.accept()

    agent_id = "<unknown>"
    codec: CryptoCodec | None = None
    projects: list[str] = []

    try:
        # ── Handshake ────────────────────────────────────────────
        agent_id, projects, codec = await _do_handshake(
            websocket, ecdsa_priv_file, ecdsa_pub_file
        )
        logger.info(f"[{agent_id}] handshake complete, projects={projects}")

        if connect_cb:
            try:
                await connect_cb(agent_id, True)
            except Exception as e:
                logger.warning(f"[{agent_id}] connect_cb error: {e}")

        # ── Register with manager ────────────────────────────────
        # Create a send closure that encrypts + sends via this websocket
        async def send_encrypted(msg_type: MessageType, msg_json: str) -> bool:
            """Send an encrypted business message to the agent. Thread-safe."""
            if codec is None:
                return False
            try:
                enc_json = codec.encrypt(msg_json.encode("utf-8"), msg_type)
                await websocket.send_text(enc_json)
                return True
            except Exception as e:
                logger.warning(f"[{agent_id}] send error: {e}")
                return False

        # We need the send callback type to match AgentManager's signature
        await manager.add_agent(agent_id, projects, send_encrypted)

        # ── Heartbeat task ──────────────────────────────────────
        async def heartbeat_loop():
            """Send periodic HEARTBEAT to detect dead connections."""
            while True:
                await asyncio.sleep(30)  # Every 30 seconds
                try:
                    await send_encrypted(
                        MessageType.HEARTBEAT,
                        json.dumps({"ts": int(asyncio.get_event_loop().time())}),
                    )
                except Exception:
                    break

        heartbeat_task = asyncio.create_task(heartbeat_loop())

        # ── I/O Loop ─────────────────────────────────────────────
        try:
            while True:
                try:
                    raw = await websocket.receive_text()
                except WebSocketDisconnect:
                    break

                # Decrypt envelope
                result = codec.decrypt(raw)
                if not result.ok:
                    logger.warning(f"[{agent_id}] decrypt error: {result.error}")
                    continue

                # Parse type
                try:
                    env = CryptoCodec.from_json(raw)
                    msg_type = MessageType.parse(env.type)
                except ValueError:
                    logger.warning(f"[{agent_id}] unknown message type: {env.type}")
                    continue

                # ── ACK: internal, just log ───────────────────────
                if msg_type == MessageType.ACK:
                    logger.debug(f"[{agent_id}] ACK received")
                    continue

                # ── HEARTBEAT: reply with ACK ────────────────────
                if msg_type == MessageType.HEARTBEAT:
                    await send_encrypted(
                        MessageType.ACK,
                        json.dumps({"ack": "heartbeat"}),
                    )
                    continue

                # ── BUILD_TRIGGER: send ACK before dispatching ────
                if msg_type == MessageType.BUILD_TRIGGER:
                    ack_payload = json.dumps({
                        "msg_id": env.msg_id,
                        "status": "received",
                    })
                    await send_encrypted(MessageType.ACK, ack_payload)

                # Dispatch business message
                if message_cb:
                    try:
                        plaintext = result.plaintext.decode("utf-8")
                        await message_cb(agent_id, msg_type, plaintext)
                    except Exception as e:
                        logger.error(f"[{agent_id}] message_cb error: {e}")
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

    except HandshakeError as e:
        logger.warning(f"[{agent_id}] handshake failed: {e}")
    except WebSocketDisconnect:
        logger.info(f"[{agent_id}] disconnected")
    except Exception as e:
        logger.error(f"[{agent_id}] unexpected error: {e}", exc_info=True)
    finally:
        # ── Unregister ───────────────────────────────────────────
        if agent_id and agent_id != "<unknown>":
            await manager.remove_agent(agent_id)
        if connect_cb and agent_id != "<unknown>":
            try:
                await connect_cb(agent_id, False)
            except Exception:
                pass


# ── Handshake ───────────────────────────────────────────────────

async def _do_handshake(
    websocket: WebSocket,
    ecdsa_priv_file: str,
    ecdsa_pub_file: str,
) -> tuple[str, list[str], CryptoCodec]:
    """Execute ECDH + ECDSA handshake. Returns (agent_id, projects, codec).

    Matches C++ AgentSession::do_handshake exactly.
    """
    # 1. Read HandshakeInit (plaintext JSON)
    raw = await websocket.receive_text()
    init = _parse_handshake_init(raw)
    agent_id = init["agent_id"]
    projects = init["projects"]

    # 2. Create verifier from Agent's ECDSA public key (PEM string)
    verifier = EcdsaSigner.from_public_key_data(
        init["ecdsa_pub_pem"].encode("utf-8")
    )

    # 3. Verify Agent's signature on handshake data
    signing_data = (
        f"{init['agent_id']}|{init['ecdh_pub_pem']}|{init['ecdsa_pub_pem']}"
    )
    signature = b64.b64_decode(init["signature_b64"])
    if not verifier.verify(signing_data.encode("utf-8"), signature):
        await _send_handshake_error(websocket, "Signature verification failed")
        raise HandshakeError("Agent signature verification failed")

    # 4. Generate Forwarder's ephemeral ECDH key pair
    ecdh = EcdhKeyExchange()

    # 5. Derive shared AES-256 key (SHA-256 over raw ECDH secret)
    shared_secret = ecdh.derive_shared_secret_pem(init["ecdh_pub_pem"])

    # 6. Load Forwarder's keys
    signer = EcdsaSigner.from_private_key_file(ecdsa_priv_file)
    with open(ecdsa_pub_file, "r", encoding="utf-8") as f:
        fwd_pub_pem = f.read()

    # 7. Sign HandshakeAck (payload: ecdh_pub_pem|ecdsa_pub_pem)
    ack_data = f"{ecdh.public_key_pem()}|{fwd_pub_pem}"
    sig = signer.sign(ack_data.encode("utf-8"))

    # 8. Send HandshakeAck (plaintext JSON)
    ack = {
        "type": "HANDSHAKE_ACK",
        "status": "OK",
        "ecdh_pub_pem": ecdh.public_key_pem(),
        "ecdsa_pub_pem": fwd_pub_pem,
        "signature": b64.b64_encode(sig),
    }
    await websocket.send_text(json.dumps(ack))

    # 9. Assemble CryptoCodec (AES key from ECDH + signer + verifier + replay guard)
    codec = CryptoCodec(
        cipher=AesGcmCipher(shared_secret),
        signer=signer,
        verifier=verifier,
        guard=ReplayGuard(),
    )

    return agent_id, projects, codec


def _parse_handshake_init(raw: str) -> dict:
    """Parse HandshakeInit JSON. Raises HandshakeError on invalid input."""
    try:
        j = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HandshakeError(f"Invalid JSON: {e}")

    required = ["agent_id", "projects", "ecdh_pub_pem", "ecdsa_pub_pem", "signature"]
    for field in required:
        if field not in j:
            raise HandshakeError(f"Missing field: {field}")
    if j.get("type") != "HANDSHAKE_INIT":
        raise HandshakeError(f"Expected HANDSHAKE_INIT, got {j.get('type')}")

    return {
        "agent_id": j["agent_id"],
        "projects": j["projects"],
        "ecdh_pub_pem": j["ecdh_pub_pem"],
        "ecdsa_pub_pem": j["ecdsa_pub_pem"],
        "signature_b64": j["signature"],
    }


async def _send_handshake_error(websocket: WebSocket, error: str) -> None:
    """Send a HandshakeAck error response."""
    try:
        err = {"type": "HANDSHAKE_ACK", "status": "ERROR", "error": error}
        await websocket.send_text(json.dumps(err))
    except Exception:
        pass
