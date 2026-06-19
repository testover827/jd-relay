"""Tests for tamper detection. Mirrors C++ test_tamper_detection.cpp.

Verifies that CryptoCodec.decrypt() rejects:
- Tampered ciphertext
- Tampered tag
- Tampered signature
- Expired timestamp
- Replay of seen nonce
- Wrong signer (imposter)
"""

import sys, os, tempfile, json, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import pytest
from forwarder.crypto import (
    CryptoEnvelope, MessageType, build_signing_payload,
    AesGcmCipher, EcdsaSigner, CryptoCodec, ReplayGuard, b64,
)


class TestTamperDetection:
    """Matches C++ tamper detection tests."""

    @pytest.fixture
    def codec_pair(self):
        aes_key = AesGcmCipher.generate_key()
        with tempfile.TemporaryDirectory() as tmp:
            priv1 = os.path.join(tmp, "priv1.pem")
            pub1 = os.path.join(tmp, "pub1.pem")
            priv2 = os.path.join(tmp, "priv2.pem")
            pub2 = os.path.join(tmp, "pub2.pem")
            EcdsaSigner.generate_keypair(priv1, pub1)
            EcdsaSigner.generate_keypair(priv2, pub2)

            codec1 = CryptoCodec(
                cipher=AesGcmCipher(aes_key),
                signer=EcdsaSigner.from_private_key_file(priv1),
                verifier=EcdsaSigner.from_public_key_file(pub2),
            )
            codec2 = CryptoCodec(
                cipher=AesGcmCipher(aes_key),
                signer=EcdsaSigner.from_private_key_file(priv2),
                verifier=EcdsaSigner.from_public_key_file(pub1),
            )
            return codec1, codec2

    def test_tampered_ciphertext_rejected(self, codec_pair):
        codec1, codec2 = codec_pair
        json_str = codec1.encrypt(b"secret", MessageType.BUILD_TRIGGER)
        j = json.loads(json_str)

        # Tamper ciphertext (flip bits in base64)
        ct = b64.b64_decode(j["ciphertext"])
        tampered_ct = bytearray(ct)
        tampered_ct[0] ^= 0xFF
        j["ciphertext"] = b64.b64_encode(bytes(tampered_ct))

        result = codec2.decrypt(json.dumps(j))
        assert not result.ok

    def test_tampered_tag_rejected(self, codec_pair):
        codec1, codec2 = codec_pair
        json_str = codec1.encrypt(b"secret", MessageType.BUILD_TRIGGER)
        j = json.loads(json_str)

        tag = b64.b64_decode(j["tag"])
        tampered_tag = bytearray(tag)
        tampered_tag[0] ^= 0xFF
        j["tag"] = b64.b64_encode(bytes(tampered_tag))

        result = codec2.decrypt(json.dumps(j))
        assert not result.ok

    def test_tampered_signature_rejected(self, codec_pair):
        codec1, codec2 = codec_pair
        json_str = codec1.encrypt(b"secret", MessageType.BUILD_TRIGGER)
        j = json.loads(json_str)

        # Replace signature with garbage
        j["signature"] = b64.b64_encode(b"\x00" * 64)

        result = codec2.decrypt(json.dumps(j))
        assert not result.ok

    def test_expired_timestamp_rejected(self, codec_pair):
        codec1, codec2 = codec_pair
        json_str = codec1.encrypt(b"secret", MessageType.BUILD_TRIGGER)
        j = json.loads(json_str)

        # Set timestamp to 10 minutes ago
        j["timestamp"] = ReplayGuard.now_ms() - 10 * 60 * 1000

        result = codec2.decrypt(json.dumps(j))
        assert not result.ok

    def test_replay_nonce_rejected(self, codec_pair):
        codec1, codec2 = codec_pair
        json_str = codec1.encrypt(b"secret", MessageType.BUILD_TRIGGER)

        # First decrypt succeeds
        result1 = codec2.decrypt(json_str)
        assert result1.ok

        # Second decrypt with same message fails (replay)
        result2 = codec2.decrypt(json_str)
        assert not result2.ok
        assert "Replay" in result2.error or "replay" in result2.error

    def test_wrong_signer_rejected(self, codec_pair):
        _, codec2 = codec_pair

        # Create an imposter with a different key
        aes_key = AesGcmCipher.generate_key()
        with tempfile.TemporaryDirectory() as tmp:
            imp_priv = os.path.join(tmp, "imp_priv.pem")
            imp_pub = os.path.join(tmp, "imp_pub.pem")
            EcdsaSigner.generate_keypair(imp_priv, imp_pub)

            imposter = CryptoCodec(
                cipher=AesGcmCipher(aes_key),
                signer=EcdsaSigner.from_private_key_file(imp_priv),
                verifier=EcdsaSigner.from_public_key_file(imp_pub),
            )

        json_str = imposter.encrypt(b"fake", MessageType.BUILD_TRIGGER)
        result = codec2.decrypt(json_str)
        assert not result.ok
        assert "signature" in result.error.lower()
