"""Manual WebSocket server — thread-based, Boost.Beast compatible.

Replaces uvicorn's websockets endpoint with a raw RFC 6455 implementation
that is verified compatible with C++ Boost.Beast WebSocket client.

Runs the WebSocket accept loop in a separate thread, sharing the
AgentManager with the FastAPI application for REST endpoints.
"""

import socket
import threading
import json
import asyncio
import logging
from typing import Callable, Awaitable

from .agent_manager import AgentManager
from .raw_ws import parse_http_upgrade, compute_accept_key, encode_frame, decode_frame, Opcode
from ..crypto import (
    CryptoCodec, MessageType, AesGcmCipher, EcdsaSigner, EcdhKeyExchange,
    ReplayGuard, b64,
)

logger = logging.getLogger(__name__)

MessageCallback = Callable[[str, MessageType, str], Awaitable[None]]
ConnectCallback = Callable[[str, bool], Awaitable[None]]


class ManualWsServer:
    """Thread-based WebSocket server compatible with Boost.Beast.

    Uses threading for I/O and an asyncio event loop in a dedicated thread
    for async callbacks (message dispatch, agent registration).
    """

    def __init__(
        self,
        host: str,
        port: int,
        ecdsa_priv_file: str,
        ecdsa_pub_file: str,
        manager: AgentManager,
        message_cb: MessageCallback | None = None,
        connect_cb: ConnectCallback | None = None,
    ):
        self._host = host
        self._port = port
        self._ecdsa_priv_file = ecdsa_priv_file
        self._ecdsa_pub_file = ecdsa_pub_file
        self._manager = manager
        self._message_cb = message_cb
        self._connect_cb = connect_cb

        self._running = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self._port

    def start(self):
        """Start the accept loop and event loop in background threads."""
        self._running.set()

        # Start asyncio event loop in a dedicated thread for callbacks
        def run_loop():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_forever()

        self._loop_thread = threading.Thread(target=run_loop, daemon=True)
        self._loop_thread.start()

        # Start WebSocket accept loop
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()
        logger.info(f"[ManualWsServer] Listening on {self._host}:{self._port}")

    def stop(self):
        """Stop the server."""
        self._running.clear()
        # Unblock accept()
        try:
            with socket.create_connection((self._host, self._port), timeout=1):
                pass
        except Exception:
            pass

        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)

        if self._thread:
            self._thread.join(timeout=5)
        if self._loop_thread:
            self._loop_thread.join(timeout=5)

    def _accept_loop(self):
        """Accept connections and handle each in a new thread."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind((self._host, self._port))
        except OSError:
            logger.error(f"[ManualWsServer] Cannot bind to {self._host}:{self._port}")
            return
        srv.listen(16)
        srv.settimeout(1.0)  # Check running flag every second

        logger.info(f"[ManualWsServer] Accept loop started on port {self._port}")
        while self._running.is_set():
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            t = threading.Thread(target=self._handle_client, args=(conn, addr),
                                 daemon=True)
            t.start()

        try:
            srv.close()
        except Exception:
            pass

    def _handle_client(self, conn: socket.socket, addr):
        """Handle one Agent connection."""
        agent_id = "<unknown>"
        codec: CryptoCodec | None = None
        projects: list[str] = []

        try:
            conn.settimeout(30)

            # WebSocket upgrade
            data = conn.recv(4096)
            headers = parse_http_upgrade(data)
            if not headers:
                conn.close()
                return

            client_key = headers.get("sec-websocket-key", "")
            if not client_key:
                conn.close()
                return

            accept = compute_accept_key(client_key)
            response = (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n"
                "\r\n"
            )
            conn.sendall(response.encode())

            # Receive HandshakeInit
            frame = _recv_frame(conn)
            if frame is None:
                conn.close()
                return
            _, init_raw = frame
            init = json.loads(init_raw)

            agent_id = init["agent_id"]
            projects = init["projects"]

            # Verify signature
            verifier = EcdsaSigner.from_public_key_data(
                init["ecdsa_pub_pem"].encode())
            sig_data = f"{agent_id}|{init['ecdh_pub_pem']}|{init['ecdsa_pub_pem']}"
            sig = b64.b64_decode(init["signature"])
            if not verifier.verify(sig_data.encode(), sig):
                _send_frame(conn, json.dumps({
                    "type": "HANDSHAKE_ACK", "status": "ERROR",
                    "error": "Signature verification failed",
                }).encode())
                conn.close()
                return

            # ECDH + HandshakeAck
            ecdh = EcdhKeyExchange()
            shared = ecdh.derive_shared_secret_pem(init["ecdh_pub_pem"])
            signer = EcdsaSigner.from_private_key_file(self._ecdsa_priv_file)
            with open(self._ecdsa_pub_file) as f:
                fwd_pub_pem = f.read()

            ack_data = f"{ecdh.public_key_pem()}|{fwd_pub_pem}"
            ack = {
                "type": "HANDSHAKE_ACK", "status": "OK",
                "ecdh_pub_pem": ecdh.public_key_pem(),
                "ecdsa_pub_pem": fwd_pub_pem,
                "signature": b64.b64_encode(signer.sign(ack_data.encode())),
            }
            _send_frame(conn, json.dumps(ack).encode())

            codec = CryptoCodec(
                AesGcmCipher(shared), signer, verifier, ReplayGuard())

            logger.info(f"[{agent_id}] Handshake complete, projects={projects}")

            # Register with manager (schedule on event loop)
            def make_send_cb(c, cd):
                def send_encrypted(msg_type: MessageType, msg_json: str) -> bool:
                    try:
                        enc_json = cd.encrypt(msg_json.encode(), msg_type)
                        _send_frame(c, enc_json.encode())
                        return True
                    except Exception:
                        return False
                return send_encrypted

            send_cb = make_send_cb(conn, codec)
            loop = self._loop
            if loop:
                future = asyncio.run_coroutine_threadsafe(
                    self._manager.add_agent(agent_id, projects, send_cb), loop)
                future.result(timeout=5)

                if self._connect_cb:
                    asyncio.run_coroutine_threadsafe(
                        self._connect_cb(agent_id, True), loop)

            # I/O loop
            conn.settimeout(30)
            while self._running.is_set():
                frame = _recv_frame(conn)
                if frame is None:
                    break
                opcode, payload = frame

                if opcode == 0x8:  # Close
                    break

                dec_text = payload.decode()
                result = codec.decrypt(dec_text)
                if not result.ok:
                    logger.warning(f"[{agent_id}] Decrypt error: {result.error}")
                    continue

                try:
                    env = CryptoCodec.from_json(dec_text)
                    msg_type = MessageType.parse(env.type)
                except ValueError:
                    continue

                if msg_type == MessageType.HEARTBEAT:
                    hb_enc = codec.encrypt(b'{"ack":"heartbeat"}', MessageType.ACK)
                    _send_frame(conn, hb_enc.encode())
                    continue

                if msg_type == MessageType.ACK:
                    continue

                if msg_type == MessageType.BUILD_TRIGGER:
                    ack_payload = json.dumps({"msg_id": env.msg_id, "status": "received"})
                    ack_enc = codec.encrypt(ack_payload.encode(), MessageType.ACK)
                    _send_frame(conn, ack_enc.encode())

                if self._message_cb and loop:
                    plaintext = result.plaintext.decode()
                    asyncio.run_coroutine_threadsafe(
                        self._message_cb(agent_id, msg_type, plaintext), loop)

        except Exception as e:
            logger.error(f"[{agent_id}] Error: {e}")
        finally:
            # Unregister (schedule on event loop)
            if loop and agent_id and agent_id != "<unknown>":
                try:
                    asyncio.run_coroutine_threadsafe(
                        self._manager.remove_agent(agent_id), loop)
                    if self._connect_cb:
                        asyncio.run_coroutine_threadsafe(
                            self._connect_cb(agent_id, False), loop)
                except Exception:
                    pass
            try:
                conn.close()
            except Exception:
                pass


def _send_frame(sock: socket.socket, payload: bytes, opcode: int = 0x1):
    """Send a WebSocket text frame."""
    frame = bytearray([0x80 | opcode])
    length = len(payload)
    if length < 126:
        frame.append(length)
    else:
        frame.append(126)
        frame.extend(length.to_bytes(2, 'big'))
    frame.extend(payload)
    sock.sendall(bytes(frame))


def _recv_frame(sock: socket.socket) -> tuple[int, bytes] | None:
    """Receive a WebSocket frame. Returns (opcode, payload) or None."""
    try:
        data = sock.recv(2)
        if len(data) < 2:
            return None
    except Exception:
        return None

    opcode = data[0] & 0x0F
    length = data[1] & 0x7F

    if length == 126:
        data = sock.recv(2)
        length = int.from_bytes(data, 'big')
    elif length == 127:
        data = sock.recv(8)
        length = int.from_bytes(data, 'big')

    mask_key = sock.recv(4)
    payload = bytearray()
    while len(payload) < length:
        chunk = sock.recv(min(length - len(payload), 16384))
        if not chunk:
            return None
        payload.extend(chunk)

    for i in range(length):
        payload[i] ^= mask_key[i % 4]

    return opcode, bytes(payload)
