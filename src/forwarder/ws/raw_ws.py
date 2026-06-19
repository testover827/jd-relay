"""Minimal RFC 6455 WebSocket implementation — no library dependency.

Replaces websockets/uvicorn for the WebSocket transport layer.
Designed to be compatible with Boost.Beast's WebSocket implementation.

The crypto/handshake layers remain unchanged — only the transport changes.
"""

import asyncio
import base64
import hashlib
import struct
import os
from enum import IntEnum

# ── WebSocket constants ──────────────────────────────────────────

class Opcode(IntEnum):
    TEXT   = 0x1
    BINARY = 0x2
    CLOSE  = 0x8
    PING   = 0x9
    PONG   = 0xA

FIN_BIT  = 0x80
MASK_BIT = 0x80

# Magic GUID for WebSocket handshake (RFC 6455 §1.3)
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


# ── Handshake ────────────────────────────────────────────────────

def compute_accept_key(client_key: str) -> str:
    """Compute Sec-WebSocket-Accept from Sec-WebSocket-Key."""
    sha1 = hashlib.sha1((client_key + WS_GUID).encode()).digest()
    return base64.b64encode(sha1).decode()


def parse_http_upgrade(data: bytes) -> dict | None:
    """Parse an HTTP upgrade request. Returns headers dict or None."""
    try:
        text = data.decode("latin-1")
    except Exception:
        return None

    lines = text.split("\r\n")
    if not lines:
        return None

    # First line: GET /path HTTP/1.1
    parts = lines[0].split(" ")
    if len(parts) < 3:
        return None

    headers = {"_method": parts[0], "_path": parts[1], "_version": parts[2]}
    for line in lines[1:]:
        if ": " in line:
            key, value = line.split(": ", 1)
            headers[key.lower()] = value
        elif line == "":
            break  # End of headers

    return headers


def build_upgrade_response(accept_key: str) -> bytes:
    """Build HTTP 101 Switching Protocols response."""
    return (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept_key}\r\n"
        "\r\n"
    ).encode()


# ── Framing ───────────────────────────────────────────────────────

def encode_frame(payload: bytes, opcode: Opcode = Opcode.TEXT) -> bytes:
    """Encode a WebSocket frame (server → client, unmasked)."""
    frame = bytearray()
    frame.append(FIN_BIT | opcode)

    length = len(payload)
    if length < 126:
        frame.append(length)
    elif length < 65536:
        frame.append(126)
        frame.extend(struct.pack("!H", length))
    else:
        frame.append(127)
        frame.extend(struct.pack("!Q", length))

    frame.extend(payload)
    return bytes(frame)


def decode_frame(data: bytes) -> tuple[Opcode, bytes] | None:
    """Decode a WebSocket frame (client → server, masked).
    Returns (opcode, payload) or None if incomplete."""
    if len(data) < 2:
        return None

    first_byte = data[0]
    second_byte = data[1]

    opcode = Opcode(first_byte & 0x0F)
    masked = (second_byte & MASK_BIT) != 0
    length = second_byte & 0x7F

    pos = 2
    if length == 126:
        if len(data) < 4:
            return None
        length = struct.unpack("!H", data[2:4])[0]
        pos = 4
    elif length == 127:
        if len(data) < 10:
            return None
        length = struct.unpack("!Q", data[2:10])[0]
        pos = 10

    if masked:
        if len(data) < pos + 4:
            return None
        mask_key = data[pos:pos + 4]
        pos += 4
    else:
        mask_key = None

    if len(data) < pos + length:
        return None

    payload = bytearray(data[pos:pos + length])
    if mask_key:
        for i in range(length):
            payload[i] ^= mask_key[i % 4]

    return opcode, bytes(payload)


# ── High-level read/write ────────────────────────────────────────

class RawWebSocket:
    """Minimal RFC 6455 WebSocket connection over an asyncio Stream."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self._reader = reader
        self._writer = writer
        self._closed = False

    @classmethod
    async def from_upgrade(cls, reader: asyncio.StreamReader,
                           writer: asyncio.StreamWriter) -> "RawWebSocket | None":
        """Perform server-side WebSocket upgrade. Returns RawWebSocket or None."""
        # Read HTTP upgrade request
        data = bytearray()
        while b"\r\n\r\n" not in data and len(data) < 8192:
            chunk = await reader.read(1024)
            if not chunk:
                return None
            data.extend(chunk)

        headers = parse_http_upgrade(bytes(data))
        if not headers:
            return None

        client_key = headers.get("sec-websocket-key", "")
        if not client_key:
            return None

        # Send upgrade response
        accept_key = compute_accept_key(client_key)
        writer.write(build_upgrade_response(accept_key))
        await writer.drain()

        return cls(reader, writer)

    async def recv(self) -> str | None:
        """Receive a text frame. Returns payload string or None on close."""
        buf = bytearray()
        while True:
            chunk = await self._reader.read(4096)
            if not chunk:
                self._closed = True
                return None
            buf.extend(chunk)
            result = decode_frame(bytes(buf))
            if result is None:
                continue  # Incomplete frame, read more

            opcode, payload = result

            if opcode == Opcode.CLOSE:
                self._closed = True
                return None
            elif opcode == Opcode.PING:
                self._writer.write(encode_frame(payload, Opcode.PONG))
                await self._writer.drain()
                buf = bytearray()  # Reset buffer for next frame
                continue
            elif opcode in (Opcode.TEXT, Opcode.BINARY):
                return payload.decode("utf-8")
            else:
                buf = bytearray()  # Unknown opcode, skip

    async def send(self, text: str) -> None:
        """Send a text frame."""
        self._writer.write(encode_frame(text.encode("utf-8"), Opcode.TEXT))
        await self._writer.drain()

    async def close(self) -> None:
        """Send close frame and close connection."""
        if not self._closed:
            self._writer.write(encode_frame(b"", Opcode.CLOSE))
            await self._writer.drain()
            self._writer.close()
            self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed
