"""AES-256-GCM cipher — wire-compatible with C++ AesGcmCipher.

Uses cryptography.hazmat.primitives.ciphers.aead.AESGCM.
Key: 32 bytes, IV: 12 bytes, Tag: 16 bytes (ciphertext and tag stored separately).
"""

from dataclasses import dataclass
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _AESGCM

from . import base64 as b64


@dataclass
class EncryptResult:
    ciphertext: bytes = b""
    iv: bytes = b""
    tag: bytes = b""
    ok: bool = False
    error: str = ""


@dataclass
class DecryptResult:
    plaintext: bytes = b""
    ok: bool = False
    error: str = ""


class AesGcmCipher:
    """AES-256-GCM cipher. Compatible with C++ AesGcmCipher."""

    KEY_SIZE = 32   # AES-256
    IV_SIZE = 12    # GCM standard nonce
    TAG_SIZE = 16   # GCM authentication tag

    def __init__(self, key: bytes):
        """Construct with a 32-byte key. Raises ValueError if key size != 32."""
        if len(key) != self.KEY_SIZE:
            raise ValueError(
                f"AES-256-GCM requires a 32-byte key, got {len(key)}"
            )
        self._key = key
        self._aead = _AESGCM(key)

    @classmethod
    def from_hex(cls, hex_key: str) -> "AesGcmCipher":
        """Construct from a 64-char hex-encoded key string."""
        key = b64.hex_decode(hex_key)
        if len(key) != cls.KEY_SIZE:
            raise ValueError(
                f"AES-256-GCM requires a 64-char hex key, got "
                f"{len(hex_key)} chars"
            )
        return cls(key)

    @staticmethod
    def generate_key() -> bytes:
        """Generate a random 32-byte key."""
        return b64.random_bytes(32)

    def encrypt(self, plaintext: bytes, iv: Optional[bytes] = None) -> EncryptResult:
        """Encrypt plaintext. Auto-generates IV if not provided.

        Args:
            plaintext: Data to encrypt.
            iv: 12-byte IV (auto-generated if None or empty).
        """
        result = EncryptResult()

        use_iv = iv if iv else b64.random_bytes(self.IV_SIZE)
        if len(use_iv) != self.IV_SIZE:
            result.error = (
                f"IV must be {self.IV_SIZE} bytes for AES-GCM, got {len(use_iv)}"
            )
            return result

        try:
            # AESGCM.encrypt returns ciphertext + tag concatenated
            ct_with_tag = self._aead.encrypt(use_iv, plaintext, None)
            # Split: last 16 bytes = tag, rest = ciphertext
            result.ciphertext = ct_with_tag[:-self.TAG_SIZE]
            result.tag = ct_with_tag[-self.TAG_SIZE:]
            result.iv = use_iv
            result.ok = True
        except Exception as e:
            result.error = f"Encryption failed: {e}"

        return result

    def decrypt(self, ciphertext: bytes, iv: bytes, tag: bytes) -> DecryptResult:
        """Decrypt ciphertext with IV and tag for authentication.

        Args:
            ciphertext: The encrypted data.
            iv: 12-byte initialization vector.
            tag: 16-byte GCM authentication tag.
        """
        result = DecryptResult()

        if len(iv) != self.IV_SIZE:
            result.error = f"IV must be {self.IV_SIZE} bytes, got {len(iv)}"
            return result
        if len(tag) != self.TAG_SIZE:
            result.error = f"Tag must be {self.TAG_SIZE} bytes, got {len(tag)}"
            return result

        try:
            ct_with_tag = ciphertext + tag
            result.plaintext = self._aead.decrypt(iv, ct_with_tag, None)
            result.ok = True
        except Exception as e:
            result.error = f"AES-GCM authentication tag verification failed: {e}"

        return result
