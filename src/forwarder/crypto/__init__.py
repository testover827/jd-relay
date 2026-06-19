"""JD-Relay crypto module — wire-compatible with C++ jd_relay_crypto library.

Provides:
- ECDH P-256 key exchange (SHA-256(raw_shared_secret) → 32-byte AES key)
- AES-256-GCM encryption (12-byte IV, 16-byte tag, ciphertext+tag separate)
- ECDSA P-256 signing/verification (DER-encoded, SHA-256 hash)
- CryptoEnvelope JSON serialization (field names and format identical to C++)
- ReplayGuard (±5 min timestamp window + nonce cache)
- CryptoCodec top-level encoder/decoder
"""

from .envelope import CryptoEnvelope, MessageType, build_signing_payload
from .aes_gcm import AesGcmCipher
from .ecdsa import EcdsaSigner
from .ecdh import EcdhKeyExchange
from .replay_guard import ReplayGuard
from .codec import CryptoCodec
from . import base64 as b64

__all__ = [
    "CryptoEnvelope",
    "MessageType",
    "build_signing_payload",
    "AesGcmCipher",
    "EcdsaSigner",
    "EcdhKeyExchange",
    "ReplayGuard",
    "CryptoCodec",
    "b64",
]
