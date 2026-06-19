"""Tests for ECDH P-256 key exchange. Mirrors C++ test_ecdh.cpp."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import pytest
from forwarder.crypto.ecdh import EcdhKeyExchange


class TestEcdhKeyExchange:
    """Matches C++ EcdhKeyExchange test cases."""

    def test_both_sides_derive_same_key(self):
        alice = EcdhKeyExchange()
        bob = EcdhKeyExchange()

        alice_key = alice.derive_shared_secret(bob.public_key_der())
        bob_key = bob.derive_shared_secret(alice.public_key_der())

        assert alice_key == bob_key
        assert len(alice_key) == 32  # AES-256 key

    def test_different_pairs_produce_different_keys(self):
        alice = EcdhKeyExchange()
        bob = EcdhKeyExchange()
        carol = EcdhKeyExchange()

        key1 = alice.derive_shared_secret(bob.public_key_der())
        key2 = alice.derive_shared_secret(carol.public_key_der())

        assert key1 != key2

    def test_pem_based_exchange(self):
        alice = EcdhKeyExchange()
        bob = EcdhKeyExchange()

        alice_key = alice.derive_shared_secret_pem(bob.public_key_pem())
        bob_key = bob.derive_shared_secret_pem(alice.public_key_pem())

        assert alice_key == bob_key
        assert len(alice_key) == 32

    def test_public_key_export_non_empty(self):
        ecdh = EcdhKeyExchange()
        der = ecdh.public_key_der()
        pem = ecdh.public_key_pem()

        assert len(der) > 0
        assert len(pem) > 0
        assert pem.startswith("-----BEGIN PUBLIC KEY-----")

    def test_load_from_private_key_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            priv_path = os.path.join(tmp, "ecdh_priv.pem")
            pub_path = os.path.join(tmp, "ecdh_pub.pem")
            EcdhKeyExchange.generate_keypair(priv_path, pub_path)

            loaded = EcdhKeyExchange.from_private_key_file(priv_path)
            peer = EcdhKeyExchange()

            key1 = loaded.derive_shared_secret(peer.public_key_der())
            key2 = peer.derive_shared_secret(loaded.public_key_der())

            assert key1 == key2
            assert len(key1) == 32
