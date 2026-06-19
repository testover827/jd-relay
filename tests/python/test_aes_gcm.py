"""Tests for AES-256-GCM cipher. Mirrors C++ test_aes_gcm.cpp."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import pytest
from forwarder.crypto.aes_gcm import AesGcmCipher
from forwarder.crypto import b64


class TestAesGcmCipher:
    """Matches C++ AesGcmCipher test cases."""

    @pytest.fixture
    def cipher(self):
        return AesGcmCipher(AesGcmCipher.generate_key())

    def test_encrypt_decrypt_roundtrip(self, cipher):
        plaintext = b"Hello, JD-Relay!"
        enc = cipher.encrypt(plaintext)
        assert enc.ok, f"Encrypt failed: {enc.error}"
        assert len(enc.iv) == 12
        assert len(enc.tag) == 16
        assert len(enc.ciphertext) == len(plaintext)

        dec = cipher.decrypt(enc.ciphertext, enc.iv, enc.tag)
        assert dec.ok, f"Decrypt failed: {dec.error}"
        assert dec.plaintext == plaintext

    def test_empty_plaintext(self, cipher):
        enc = cipher.encrypt(b"")
        assert enc.ok
        dec = cipher.decrypt(enc.ciphertext, enc.iv, enc.tag)
        assert dec.ok
        assert dec.plaintext == b""

    def test_large_payload(self, cipher):
        plaintext = b"A" * 65536  # 64 KiB
        enc = cipher.encrypt(plaintext)
        assert enc.ok
        dec = cipher.decrypt(enc.ciphertext, enc.iv, enc.tag)
        assert dec.ok
        assert dec.plaintext == plaintext

    def test_wrong_key_decrypt_fails(self, cipher):
        plaintext = b"secret data"
        enc = cipher.encrypt(plaintext)

        wrong_cipher = AesGcmCipher(AesGcmCipher.generate_key())
        dec = wrong_cipher.decrypt(enc.ciphertext, enc.iv, enc.tag)
        assert not dec.ok
        assert "tag" in dec.error.lower()

    def test_tampered_ciphertext(self, cipher):
        enc = cipher.encrypt(b"secret data")
        tampered = bytearray(enc.ciphertext)
        tampered[0] ^= 0xFF
        dec = cipher.decrypt(bytes(tampered), enc.iv, enc.tag)
        assert not dec.ok

    def test_tampered_tag(self, cipher):
        enc = cipher.encrypt(b"secret data")
        tampered = bytearray(enc.tag)
        tampered[0] ^= 0xFF
        dec = cipher.decrypt(enc.ciphertext, enc.iv, bytes(tampered))
        assert not dec.ok

    def test_tampered_iv(self, cipher):
        enc = cipher.encrypt(b"secret data")
        tampered = bytearray(enc.iv)
        tampered[0] ^= 0xFF
        dec = cipher.decrypt(enc.ciphertext, bytes(tampered), enc.tag)
        assert not dec.ok

    def test_hex_key_constructor(self):
        key_hex = b64.hex_encode(AesGcmCipher.generate_key())
        cipher = AesGcmCipher.from_hex(key_hex)
        plaintext = b"test"
        enc = cipher.encrypt(plaintext)
        assert enc.ok
        dec = cipher.decrypt(enc.ciphertext, enc.iv, enc.tag)
        assert dec.ok
        assert dec.plaintext == plaintext

    def test_invalid_key_size_raises(self):
        with pytest.raises(ValueError):
            AesGcmCipher(b"too-short")
        with pytest.raises(ValueError):
            AesGcmCipher(b"this-key-is-way-too-long-for-aes-256")
