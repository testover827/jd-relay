"""Base64 / Hex / UUID / random utilities.

Wire-compatible with C++ jd_relay::crypto base64 functions.
C++ uses OpenSSL BIO base64 which adds \\n line breaks every 64 chars.
Python MUST match this exactly — the signing payload includes these base64
strings verbatim, so any difference breaks cross-language signature verification.

Key design:
- encode() adds \\n every 64 chars + trailing \\n (matches OpenSSL BIO_f_base64 default)
- decode() strips whitespace before decoding (handles both formats)
"""

import base64 as _b64
import os
import uuid


def b64_encode(data: bytes) -> str:
    """Base64 encode matching OpenSSL BIO_f_base64 default format:
    adds newline every 64 chars and a trailing newline."""
    raw = _b64.b64encode(data).decode("ascii")
    # Split into 64-char lines + trailing newline (like OpenSSL BIO)
    lines = [raw[i:i+64] for i in range(0, len(raw), 64)]
    return "\n".join(lines) + "\n"


def b64_decode(encoded: str) -> bytes:
    """Base64 decode. Strips whitespace first so C++'s
    newline-containing base64 can be decoded."""
    cleaned = encoded.replace("\n", "").replace("\r", "").replace(" ", "")
    return _b64.b64decode(cleaned)


def hex_encode(data: bytes) -> str:
    """Hex encode (lowercase, matches C++)."""
    return data.hex()


def hex_decode(hex_str: str) -> bytes:
    """Hex decode (case-insensitive, matches C++)."""
    return bytes.fromhex(hex_str)


def random_bytes(count: int) -> bytes:
    """Generate cryptographically secure random bytes using OS CSPRNG."""
    return os.urandom(count)


def generate_uuid() -> str:
    """Generate UUID v4 string (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx).
    Matches C++ format exactly."""
    return str(uuid.uuid4())
