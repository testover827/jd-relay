"""Test Boost.Beast ↔ manual RFC 6455 WebSocket (thread-based server)."""
import sys, os, json, tempfile, threading, subprocess, socket, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import pytest
from forwarder.crypto import *
from forwarder.ws.raw_ws import parse_http_upgrade, compute_accept_key
from forwarder.ws.raw_ws import encode_frame, decode_frame, Opcode

CROSS_WS_BIN = '/mnt/d/workspace/jd-relay/build_wsl/bin/test_cross_ws'


def _to_wsl(p: str) -> str:
    drive, rest = os.path.splitdrive(os.path.normpath(p))
    return '/mnt/' + drive[0].lower() + rest.replace('\\', '/')


def _wsl_gateway() -> str:
    r = subprocess.run(
        ['wsl', '-e', 'bash', '-c', "ip route show default | awk '{print $3}'"],
        capture_output=True, text=True, encoding='utf-8', timeout=5)
    return r.stdout.strip() or '172.30.160.1'


def ws_send_frame(sock: socket.socket, payload: bytes, opcode: int = 0x1):
    """Send a WebSocket text frame (server → client, unmasked)."""
    frame = bytearray([0x80 | opcode])
    length = len(payload)
    if length < 126:
        frame.append(length)
    else:
        frame.append(126)
        frame.extend(length.to_bytes(2, 'big'))
    frame.extend(payload)
    sock.sendall(bytes(frame))


def ws_recv_frame(sock: socket.socket) -> tuple[int, bytes] | None:
    """Receive a WebSocket frame (client → server, masked).
    Returns (opcode, payload) or None on error."""
    try:
        data = sock.recv(2)
        if len(data) < 2:
            return None
    except Exception:
        return None

    first_byte = data[0]
    second_byte = data[1]
    opcode = first_byte & 0x0F
    length = second_byte & 0x7F

    if length == 126:
        data = sock.recv(2)
        length = int.from_bytes(data, 'big')
    elif length == 127:
        data = sock.recv(8)
        length = int.from_bytes(data, 'big')

    mask_key = sock.recv(4)
    payload = bytearray()
    while len(payload) < length:
        chunk = sock.recv(min(length - len(payload), 4096))
        if not chunk:
            return None
        payload.extend(chunk)

    for i in range(length):
        payload[i] ^= mask_key[i % 4]

    return opcode, bytes(payload)


def ws_handshake(conn: socket.socket) -> bool:
    """Perform WebSocket server-side upgrade handshake. Returns True on success."""
    data = conn.recv(4096)
    headers = parse_http_upgrade(data)
    if not headers:
        return False

    client_key = headers.get("sec-websocket-key", "")
    if not client_key:
        return False

    accept = compute_accept_key(client_key)
    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n"
        "\r\n"
    )
    conn.sendall(response.encode())
    return True


class TestWsCompat:

    def test_manual_ws_with_cpp_client(self):
        """Thread-based manual WebSocket server ← C++ Boost.Beast client."""
        with tempfile.TemporaryDirectory() as tmp:
            fwd_priv = os.path.join(tmp, 'fwd_priv.pem')
            fwd_pub = os.path.join(tmp, 'fwd_pub.pem')
            EcdsaSigner.generate_keypair(fwd_priv, fwd_pub)
            with open(fwd_pub) as f:
                fwd_pub_pem = f.read()

            agt_priv = os.path.join(tmp, 'agt_priv.pem')
            agt_pub = os.path.join(tmp, 'agt_pub.pem')
            EcdsaSigner.generate_keypair(agt_priv, agt_pub)

            result_container = {"received": False}

            def server_thread():
                srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                srv.bind(('0.0.0.0', port))
                srv.listen(1)
                srv.settimeout(15)

                try:
                    conn, addr = srv.accept()
                except socket.timeout:
                    srv.close()
                    return

                # WebSocket upgrade
                if not ws_handshake(conn):
                    conn.close()
                    srv.close()
                    return

                # Receive HandshakeInit
                frame = ws_recv_frame(conn)
                if frame is None:
                    conn.close()
                    srv.close()
                    return
                _, init_raw = frame
                init = json.loads(init_raw)

                # Verify signature + create HandshakeAck
                verifier = EcdsaSigner.from_public_key_data(
                    init['ecdsa_pub_pem'].encode())
                sig_data = f"{init['agent_id']}|{init['ecdh_pub_pem']}|{init['ecdsa_pub_pem']}"
                sig = b64.b64_decode(init['signature'])
                if not verifier.verify(sig_data.encode(), sig):
                    ws_send_frame(conn, json.dumps({
                        'type': 'HANDSHAKE_ACK', 'status': 'ERROR'
                    }).encode())
                    conn.close()
                    srv.close()
                    return

                ecdh = EcdhKeyExchange()
                shared = ecdh.derive_shared_secret_pem(init['ecdh_pub_pem'])
                signer = EcdsaSigner.from_private_key_file(fwd_priv)
                ack_data = f"{ecdh.public_key_pem()}|{fwd_pub_pem}"
                ack = {
                    'type': 'HANDSHAKE_ACK', 'status': 'OK',
                    'ecdh_pub_pem': ecdh.public_key_pem(),
                    'ecdsa_pub_pem': fwd_pub_pem,
                    'signature': b64.b64_encode(signer.sign(ack_data.encode())),
                }
                ws_send_frame(conn, json.dumps(ack).encode())

                # Receive encrypted message
                frame = ws_recv_frame(conn)
                if frame:
                    codec = CryptoCodec(AesGcmCipher(shared), signer, verifier)
                    _, enc = frame
                    result = codec.decrypt(enc.decode())
                    if result.ok:
                        result_container["received"] = True

                conn.close()
                srv.close()

            # Find port
            with socket.socket() as s:
                s.bind(('0.0.0.0', 0))
                port = s.getsockname()[1]

            t = threading.Thread(target=server_thread, daemon=True)
            t.start()
            time.sleep(0.1)

            host_ip = _wsl_gateway()

            # Run C++ client
            result = subprocess.run(
                ['wsl', '-e', CROSS_WS_BIN,
                 host_ip, str(port),
                 _to_wsl(agt_priv), _to_wsl(agt_pub), _to_wsl(fwd_pub)],
                capture_output=True, text=True, encoding='utf-8', timeout=30,
            )

            t.join(timeout=5)

            print(f'C++ rc={result.returncode}')
            print(f'C++ stdout: {result.stdout[:500]}')
            if result.stderr:
                print(f'C++ stderr: {result.stderr[:500]}')

            assert result.returncode == 0, (
                f"C++ test failed (rc={result.returncode}):\n"
                f"stdout={result.stdout}\nstderr={result.stderr}"
            )
            assert result_container["received"], (
                "Server did not receive message from C++ client"
            )
