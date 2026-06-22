"""ECDH P-256 key exchange — wire-compatible with C++ EcdhKeyExchange.

Flow:
1. Each party generates an ephemeral P-256 key pair
2. Exchange public keys (DER SubjectPublicKeyInfo format, or PEM)
3. Each derives the raw ECDH shared secret
4. SHA-256(raw_shared_secret) → 32-byte AES-256 session key
"""

import hashlib

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization


class EcdhKeyExchange:
    """ECDH P-256 key exchange. Generates ephemeral key pair on construction."""

    def __init__(self):
        """Generate a new ephemeral P-256 key pair."""
        self._private_key = ec.generate_private_key(ec.SECP256R1())

    @classmethod
    def from_private_key_file(cls, pem_path: str) -> "EcdhKeyExchange":
        """Load from an existing PEM private key file."""
        with open(pem_path, "rb") as f:
            pem_data = f.read()
        instance = cls.__new__(cls)
        instance._private_key = serialization.load_pem_private_key(
            pem_data, password=None
        )
        return instance

    def public_key_der(self) -> bytes:
        """Get our public key in SubjectPublicKeyInfo DER format.
        Matches C++ i2d_PUBKEY output."""
        return self._private_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    def public_key_pem(self) -> str:
        """Get our public key in PEM format."""
        return self._private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("ascii")

    def derive_shared_secret(self, peer_pub_der: bytes) -> bytes:
        """Derive AES-256 key from peer's public key (DER format).

        Steps:
        1. Load peer's public key from DER
        2. ECDH key exchange to get raw shared secret
        3. SHA-256(raw_shared_secret) → 32-byte AES key

        Matches C++ EcdhKeyExchange::derive_shared_secret() exactly.
        """
        peer_key = serialization.load_der_public_key(peer_pub_der)
        raw_secret = self._private_key.exchange(ec.ECDH(), peer_key)
        # SHA-256(raw_shared_secret) → 32-byte AES key
        return hashlib.sha256(raw_secret).digest()

    def derive_shared_secret_pem(self, peer_pub_pem: str) -> bytes:
        """Convenience: derive shared secret from peer's PEM public key."""
        peer_key = serialization.load_pem_public_key(
            peer_pub_pem.encode("ascii")
        )
        raw_secret = self._private_key.exchange(ec.ECDH(), peer_key)
        return hashlib.sha256(raw_secret).digest()

    @staticmethod
    def generate_keypair(private_pem_path: str, public_pem_path: str) -> None:
        """Generate a new P-256 key pair and save to PEM files."""
        private_key = ec.generate_private_key(ec.SECP256R1())

        with open(private_pem_path, "wb") as f:
            f.write(
                private_key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )

        with open(public_pem_path, "wb") as f:
            f.write(
                private_key.public_key().public_bytes(
                    serialization.Encoding.PEM,
                    serialization.PublicFormat.SubjectPublicKeyInfo,
                )
            )
