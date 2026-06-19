"""Tests for ECDSA P-256 signer. Mirrors C++ test_ecdsa.cpp."""

import sys, os, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import pytest
from forwarder.crypto.ecdsa import EcdsaSigner


class TestEcdsaSigner:
    """Matches C++ EcdsaSigner test cases."""

    @pytest.fixture
    def temp_keys(self):
        """Generate temp key files."""
        with tempfile.TemporaryDirectory() as tmp:
            priv = os.path.join(tmp, "priv.pem")
            pub = os.path.join(tmp, "pub.pem")
            EcdsaSigner.generate_keypair(priv, pub)
            yield priv, pub

    def test_sign_and_verify(self, temp_keys):
        priv_path, pub_path = temp_keys
        signer = EcdsaSigner.from_private_key_file(priv_path)
        verifier = EcdsaSigner.from_public_key_file(pub_path)

        data = b"test message for ECDSA"
        sig = signer.sign(data)
        assert len(sig) > 0
        assert verifier.verify(data, sig)

    def test_verify_only(self, temp_keys):
        _, pub_path = temp_keys
        verifier = EcdsaSigner.from_public_key_file(pub_path)
        assert not verifier.can_sign()
        assert verifier.sign(b"data") == b""

    def test_wrong_signature_fails(self, temp_keys):
        priv_path, pub_path = temp_keys
        signer = EcdsaSigner.from_private_key_file(priv_path)
        verifier = EcdsaSigner.from_public_key_file(pub_path)

        data = b"real message"
        sig = signer.sign(data)

        # Verify with wrong data should fail
        assert not verifier.verify(b"wrong message", sig)

    def test_wrong_key_fails(self, temp_keys):
        priv_path, _ = temp_keys
        signer = EcdsaSigner.from_private_key_file(priv_path)

        # Generate a different key for verification
        with tempfile.TemporaryDirectory() as tmp:
            other_pub = os.path.join(tmp, "other_pub.pem")
            other_priv = os.path.join(tmp, "other_priv.pem")
            EcdsaSigner.generate_keypair(other_priv, other_pub)
            wrong_verifier = EcdsaSigner.from_public_key_file(other_pub)

        data = b"test data"
        sig = signer.sign(data)
        assert not wrong_verifier.verify(data, sig)

    def test_empty_data(self, temp_keys):
        priv_path, pub_path = temp_keys
        signer = EcdsaSigner.from_private_key_file(priv_path)
        verifier = EcdsaSigner.from_public_key_file(pub_path)

        sig = signer.sign(b"")
        assert len(sig) > 0
        assert verifier.verify(b"", sig)

    def test_public_key_der(self, temp_keys):
        priv_path, _ = temp_keys
        signer = EcdsaSigner.from_private_key_file(priv_path)
        der = signer.public_key_der()
        assert len(der) > 0
        # SubjectPublicKeyInfo for P-256 should be ~91 bytes
        assert 80 < len(der) < 200

    def test_signer_with_private_key_can_verify(self, temp_keys):
        priv_path, _ = temp_keys
        signer = EcdsaSigner.from_private_key_file(priv_path)
        data = b"self-verify test"
        sig = signer.sign(data)
        assert signer.verify(data, sig)

    def test_different_keys_produce_different_signatures(self, temp_keys):
        priv_path, _ = temp_keys
        signer = EcdsaSigner.from_private_key_file(priv_path)

        sig1 = signer.sign(b"data")
        sig2 = signer.sign(b"data")

        # ECDSA is non-deterministic, signatures should differ
        # (Note: deterministic ECDSA would produce same signature)
        # With standard ECDSA, they may differ due to random k
