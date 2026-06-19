"""Tests for CryptoEnvelope and CryptoCodec roundtrip. Mirrors C++ test_envelope_roundtrip.cpp."""

import sys, os, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import pytest
import json
from forwarder.crypto import (
    CryptoEnvelope, MessageType, build_signing_payload,
    AesGcmCipher, EcdsaSigner, CryptoCodec, b64,
)


class TestEnvelopeRoundtrip:
    """Matches C++ envelope roundtrip tests."""

    @pytest.fixture
    def codec_pair(self):
        """Create two compatible codecs (simulating Forwarder and Agent)."""
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
            yield codec1, codec2

    def test_basic_encrypt_decrypt(self, codec_pair):
        codec1, codec2 = codec_pair
        plaintext = b"Hello, encrypted world!"
        json_str = codec1.encrypt(plaintext, MessageType.BUILD_TRIGGER)
        result = codec2.decrypt(json_str)
        assert result.ok, f"Decrypt failed: {result.error}"
        assert result.plaintext == plaintext

    def test_json_serialization_roundtrip(self, codec_pair):
        codec1, _ = codec_pair
        json_str = codec1.encrypt(b"test", MessageType.BUILD_TRIGGER)

        # Parse the JSON and check all required fields
        j = json.loads(json_str)
        assert "msg_id" in j
        assert "timestamp" in j
        assert "nonce" in j
        assert "type" in j
        assert "iv" in j
        assert "ciphertext" in j
        assert "tag" in j
        assert "signature" in j

        # msg_id should be a UUID v4 format
        assert len(j["msg_id"]) == 36
        assert j["msg_id"].count("-") == 4

    def test_all_message_types(self, codec_pair):
        codec1, codec2 = codec_pair
        for msg_type in MessageType:
            json_str = codec1.encrypt(b"test", msg_type)
            # Parse and verify type field
            j = json.loads(json_str)
            assert j["type"] == msg_type.value
            # Decrypt
            result = codec2.decrypt(json_str)
            assert result.ok, f"Failed for type {msg_type}: {result.error}"
            assert result.plaintext == b"test"

    def test_empty_plaintext(self, codec_pair):
        codec1, codec2 = codec_pair
        json_str = codec1.encrypt(b"", MessageType.HEARTBEAT)
        result = codec2.decrypt(json_str)
        assert result.ok
        assert result.plaintext == b""

    def test_large_payload(self, codec_pair):
        codec1, codec2 = codec_pair
        plaintext = b"X" * (1024 * 1024)  # 1 MiB
        json_str = codec1.encrypt(plaintext, MessageType.BUILD_RESULT)
        result = codec2.decrypt(json_str)
        assert result.ok
        assert result.plaintext == plaintext

    def test_signing_payload_format(self):
        """Verify signing payload format matches C++."""
        env = CryptoEnvelope(
            msg_id="test-123",
            timestamp=1234567890,
            nonce="bm9uY2U=",
            type="BUILD_TRIGGER",
            iv="aXY=",
            ciphertext="Y3Q=",
            tag="dGFn",
        )
        payload = build_signing_payload(env)
        expected = "test-123|1234567890|bm9uY2U=|BUILD_TRIGGER|aXY=|Y3Q=|dGFn"
        assert payload == expected
