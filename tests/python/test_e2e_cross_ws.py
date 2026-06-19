"""End-to-end cross-language test: Python ManualWsServer ↔ C++ Agent.

Uses the thread-based manual WebSocket server (Boost.Beast compatible)
validated in test_ws_compat.py.
"""

import sys, os, json, tempfile, asyncio, subprocess, socket, threading, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import pytest
from forwarder.crypto import *
from forwarder.ws.agent_manager import AgentManager
from forwarder.ws.manual_server import ManualWsServer

CROSS_WS_BIN = '/mnt/d/workspace/jd-relay/build_wsl/bin/test_cross_ws'


def _to_wsl(p: str) -> str:
    drive, rest = os.path.splitdrive(os.path.normpath(p))
    return '/mnt/' + drive[0].lower() + rest.replace('\\', '/')


def _wsl_gateway() -> str:
    r = subprocess.run(
        ['wsl', '-e', 'bash', '-c', "ip route show default | awk '{print $3}'"],
        capture_output=True, text=True, encoding='utf-8', timeout=5)
    return r.stdout.strip() or '172.30.160.1'


class TestE2ECrossLanguage:

    def test_cpp_agent_handshake_and_message(self):
        """C++ Agent connects to Python ManualWsServer, handshakes, exchanges messages."""
        with tempfile.TemporaryDirectory() as tmp:
            fwd_priv = os.path.join(tmp, 'fwd_priv.pem')
            fwd_pub = os.path.join(tmp, 'fwd_pub.pem')
            EcdsaSigner.generate_keypair(fwd_priv, fwd_pub)

            agt_priv = os.path.join(tmp, 'agt_priv.pem')
            agt_pub = os.path.join(tmp, 'agt_pub.pem')
            EcdsaSigner.generate_keypair(agt_priv, agt_pub)

            # Collect received messages
            received: list[dict] = []
            async def msg_cb(agent_id: str, msg_type: MessageType, plaintext: str):
                received.append({
                    "agent_id": agent_id,
                    "type": msg_type,
                    "plaintext": plaintext,
                })

            # Create agent manager and manual WS server
            manager = AgentManager()

            # Set up asyncio for the main thread's coroutine dispatch
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            with socket.socket() as s:
                s.bind(('0.0.0.0', 0))
                port = s.getsockname()[1]

            server = ManualWsServer(
                host='0.0.0.0',
                port=port,
                ecdsa_priv_file=fwd_priv,
                ecdsa_pub_file=fwd_pub,
                manager=manager,
                message_cb=msg_cb,
            )
            server.start()
            time.sleep(0.1)

            host_ip = _wsl_gateway()

            # Run C++ Agent
            result = subprocess.run(
                ['wsl', '-e', CROSS_WS_BIN,
                 host_ip, str(port),
                 _to_wsl(agt_priv), _to_wsl(agt_pub), _to_wsl(fwd_pub)],
                capture_output=True, text=True, encoding='utf-8', timeout=30,
            )

            server.stop()

            print(f'\nC++ Agent stdout:\n{result.stdout}')
            if result.stderr:
                print(f'C++ Agent stderr:\n{result.stderr}')

            assert result.returncode == 0, (
                f"C++ Agent test failed (rc={result.returncode}):\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )

            # Verify Forwarder received messages
            assert len(received) >= 1, (
                f"No messages received from C++ Agent. received={received}"
            )
            assert received[0]["agent_id"] == "cpp-agent-001"
            assert received[0]["type"] == MessageType.BUILD_RESULT
            assert "WO-CROSS-001" in received[0]["plaintext"]
            assert "ALL TESTS PASSED" in result.stdout
