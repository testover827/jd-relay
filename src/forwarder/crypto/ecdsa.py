"""ECDSA P-256 signer — wire-compatible with C++ EcdsaSigner.

Uses DER-encoded signatures, SHA-256 hash. PEM-formatted key files.
Public key export uses SubjectPublicKeyInfo DER (matches C++ i2d_PUBKEY).
"""

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes, serialization


class EcdsaSigner:
    """ECDSA P-256 signer. Can be signing-capable (has private key)
    or verify-only (has only public key)."""

    ALGORITHM = "ECDSA-P256"

    def __init__(self):
        """Default constructor (verify-only, load keys later)."""
        self._private_key: ec.EllipticCurvePrivateKey | None = None
        self._public_key: ec.EllipticCurvePublicKey | None = None

    @classmethod
    def from_private_key_file(cls, pem_path: str) -> "EcdsaSigner":
        """Construct from a PEM-encoded private key file (can sign and verify)."""
        with open(pem_path, "rb") as f:
            return cls.from_private_key_data(f.read())

    @classmethod
    def from_private_key_data(cls, pem_data: bytes) -> "EcdsaSigner":
        """Construct from in-memory PEM private key data."""
        instance = cls()
        if pem_data:
            instance._private_key = serialization.load_pem_private_key(
                pem_data, password=None
            )
        return instance

    @classmethod
    def from_public_key_file(cls, pem_path: str) -> "EcdsaSigner":
        """Construct for verification-only from a PEM public key file."""
        with open(pem_path, "rb") as f:
            return cls.from_public_key_data(f.read())

    @classmethod
    def from_public_key_data(cls, pem_data: bytes) -> "EcdsaSigner":
        """Construct for verification-only from in-memory PEM public key data."""
        instance = cls()
        if pem_data:
            instance._public_key = serialization.load_pem_public_key(pem_data)
        return instance

    def can_sign(self) -> bool:
        """True if this instance has a private key for signing."""
        return self._private_key is not None

    def sign(self, data: bytes) -> bytes:
        """Sign data using SHA-256 hash. Returns DER-encoded signature.
        Returns empty bytes if no private key."""
        if not self._private_key:
            return b""
        return self._private_key.sign(data, ec.ECDSA(hashes.SHA256()))

    def verify(self, data: bytes, signature: bytes) -> bool:
        """Verify a DER-encoded ECDSA signature over data.
        Uses public key if available, otherwise falls back to private key."""
        key = self._public_key or self._private_key
        if key is None:
            return False
        try:
            # Must use the public key for verification
            pub_key = key if isinstance(key, ec.EllipticCurvePublicKey) else (
                self._private_key.public_key() if self._private_key else None
            )
            if pub_key is None:
                return False
            pub_key.verify(signature, data, ec.ECDSA(hashes.SHA256()))
            return True
        except Exception:
            return False

    def public_key_der(self) -> bytes:
        """Get public key in SubjectPublicKeyInfo DER format.
        Matches C++ i2d_PUBKEY output."""
        key = self._public_key or (
            self._private_key.public_key() if self._private_key else None
        )
        if key is None:
            return b""
        return key.public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    @staticmethod
    def generate_keypair(private_pem_path: str, public_pem_path: str) -> None:
        """Generate a new P-256 key pair and write to PEM files."""
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
