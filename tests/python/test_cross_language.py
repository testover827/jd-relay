"""Cross-language interoperability tests: Python ↔ C++ crypto.

Tests that Python crypto module is wire-compatible with C++ jd_relay_crypto.
Uses C++ encryptor/decryptor/keygen CLI tools via WSL (Linux ELF binaries).

Prerequisites: C++ tools built at build/bin/{keygen,encryptor,decryptor}
"""

import sys, os, json, subprocess, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import pytest
from forwarder.crypto import (
    CryptoCodec, CryptoEnvelope, MessageType, build_signing_payload,
    AesGcmCipher, EcdsaSigner, EcdhKeyExchange, ReplayGuard, b64,
)

# ── C++ tool discovery ──────────────────────────────────────────

_BUILD_DIR = os.path.normpath(os.path.join(
    os.path.dirname(__file__), '..', '..', 'build', 'bin'
))
_USE_WSL = sys.platform == 'win32'


def _to_wsl_path(p: str) -> str:
    """C:\\foo\\bar → /mnt/c/foo/bar"""
    drive, rest = os.path.splitdrive(os.path.normpath(p))
    return '/mnt/' + drive[0].lower() + rest.replace('\\', '/')


def _cpp_tool(tool_name: str, *args, input_text: str | None = None):
    """Run a C++ CLI tool (via WSL if on Windows). Returns (stdout, stderr, rc)."""
    tool_path = os.path.join(_BUILD_DIR, tool_name)

    if _USE_WSL:
        wsl_tool = _to_wsl_path(tool_path)
        cmd = ['wsl', '-e', wsl_tool]
        for a in args:
            # Convert file paths to WSL format
            a_str = str(a)
            if os.path.exists(a_str):
                cmd.append(_to_wsl_path(a_str))
            else:
                cmd.append(a_str)
    else:
        cmd = [tool_path] + [str(a) for a in args]

    result = subprocess.run(
        cmd,
        input=input_text,
        capture_output=True,
        text=True,
        encoding='utf-8',
        timeout=15,
    )
    return result.stdout, result.stderr, result.returncode


# ── Fixture ─────────────────────────────────────────────────────

@pytest.fixture
def cpp_keys():
    """Generate keys using C++ keygen tool."""
    tmp = tempfile.mkdtemp()
    stdout, stderr, rc = _cpp_tool('keygen', tmp)
    if rc != 0 or not stdout:
        pytest.skip(f"C++ keygen failed (rc={rc}): stderr={stderr}, stdout={stdout}")

    # keygen output: prints AES key hex prefixed by "  " after the AES header line
    # Format:
    #   AES-256 session key (set as RELAY_AES_KEY env var):
    #     HEX_KEY_64_CHARS
    aes_key_hex = ""
    for line in stdout.split("\n"):
        line = line.strip()
        # 64 hex chars = 32 bytes AES-256 key
        if len(line) == 64 and all(c in "0123456789abcdefABCDEF" for c in line):
            aes_key_hex = line
            break

    if not aes_key_hex:
        pytest.skip(f"Could not parse AES key from keygen output: {stdout[:200]}")

    # keygen creates: ecdsa_private.pem, ecdsa_public.pem,
    #                  ecdh_private.pem, ecdh_public.pem
    return {
        "dir": tmp,
        "aes_key_hex": aes_key_hex,
        "ecdsa_priv": os.path.join(tmp, "ecdsa_private.pem"),
        "ecdsa_pub": os.path.join(tmp, "ecdsa_public.pem"),
        "ecdh_priv": os.path.join(tmp, "ecdh_private.pem"),
        "ecdh_pub": os.path.join(tmp, "ecdh_public.pem"),
    }


# ── Tests ───────────────────────────────────────────────────────

class TestCrossLanguage:

    def test_python_encrypt_cpp_decrypt(self, cpp_keys):
        """Python encrypt → C++ decryptor decrypts."""
        k = cpp_keys
        aes_key = b64.hex_decode(k["aes_key_hex"])
        signer = EcdsaSigner.from_private_key_file(k["ecdsa_priv"])
        verifier = EcdsaSigner.from_public_key_file(k["ecdsa_pub"])

        codec = CryptoCodec(
            cipher=AesGcmCipher(aes_key),
            signer=signer,
            verifier=verifier,
        )

        plaintext = b"Hello from Python to C++!"
        enc_json = codec.encrypt(plaintext, MessageType.BUILD_TRIGGER)

        stdout, stderr, rc = _cpp_tool(
            'decryptor',
            '--ecdsa-key', k["ecdsa_priv"],
            '--peer-pub', k["ecdsa_pub"],
            '--aes-key', k["aes_key_hex"],
            input_text=enc_json,
        )

        assert rc == 0, f"C++ decryptor failed (rc={rc}):\nstderr: {stderr}\nstdout: {stdout}"
        assert plaintext.decode() in stdout

    def test_cpp_encrypt_python_decrypt(self, cpp_keys):
        """C++ encryptor encrypts → Python decrypts."""
        k = cpp_keys
        plaintext = "Hello from C++ to Python!"

        stdout, stderr, rc = _cpp_tool(
            'encryptor',
            '--ecdsa-key', k["ecdsa_priv"],
            '--peer-pub', k["ecdsa_pub"],
            '--aes-key', k["aes_key_hex"],
            '--type', 'BUILD_TRIGGER',
            input_text=plaintext,
        )

        assert rc == 0, f"C++ encryptor failed (rc={rc}):\nstderr: {stderr}"
        enc_json = stdout.strip()

        # Python decrypts
        aes_key = b64.hex_decode(k["aes_key_hex"])
        signer = EcdsaSigner.from_private_key_file(k["ecdsa_priv"])
        verifier = EcdsaSigner.from_public_key_file(k["ecdsa_pub"])

        codec = CryptoCodec(
            cipher=AesGcmCipher(aes_key),
            signer=signer,
            verifier=verifier,
        )
        dec = codec.decrypt(enc_json)
        assert dec.ok, f"Python decrypt failed: {dec.error}"
        assert dec.plaintext.decode() == plaintext

    def test_ecdh_shared_secret_consistency(self, cpp_keys):
        """Python ECDH derives valid key from C++ ECDH public key."""
        py_ecdh = EcdhKeyExchange()

        with open(cpp_keys["ecdh_pub"], "r") as f:
            cpp_pub_pem = f.read()

        # Python derives shared secret from C++ public key
        shared = py_ecdh.derive_shared_secret_pem(cpp_pub_pem)
        assert len(shared) == 32
        # Deterministic: same input → same output
        shared2 = py_ecdh.derive_shared_secret_pem(cpp_pub_pem)
        assert shared == shared2

    def test_ecdsa_signature_format_consistency(self, cpp_keys):
        """Python ECDSA signatures are DER-encoded (starts with 0x30)."""
        signer = EcdsaSigner.from_private_key_file(cpp_keys["ecdsa_priv"])
        verifier = EcdsaSigner.from_public_key_file(cpp_keys["ecdsa_pub"])

        data = b"Cross-language ECDSA test payload"
        signature = signer.sign(data)

        # DER SEQUENCE tag
        assert signature[0] == 0x30, f"Not DER: {signature[:4].hex()}"
        # Self-verify
        assert verifier.verify(data, signature)

    def test_envelope_json_format_consistency(self, cpp_keys):
        """CryptoEnvelope JSON has all required fields with correct types."""
        k = cpp_keys
        aes_key = b64.hex_decode(k["aes_key_hex"])
        signer = EcdsaSigner.from_private_key_file(k["ecdsa_priv"])
        verifier = EcdsaSigner.from_public_key_file(k["ecdsa_pub"])

        codec = CryptoCodec(
            cipher=AesGcmCipher(aes_key),
            signer=signer,
            verifier=verifier,
        )

        enc_json = codec.encrypt(b"format test", MessageType.BUILD_TRIGGER)
        j = json.loads(enc_json)

        assert isinstance(j["msg_id"], str)
        assert isinstance(j["timestamp"], int)
        assert isinstance(j["nonce"], str)
        assert j["type"] == "BUILD_TRIGGER"
        assert isinstance(j["iv"], str)
        assert isinstance(j["ciphertext"], str)
        assert isinstance(j["tag"], str)
        assert isinstance(j["signature"], str)

        # UUID v4 format
        assert len(j["msg_id"]) == 36
        assert len(j["msg_id"].split("-")) == 5

    def test_all_message_types_cross(self, cpp_keys):
        """All 6 message types survive Python encrypt → C++ decrypt roundtrip."""
        k = cpp_keys
        aes_key = b64.hex_decode(k["aes_key_hex"])
        signer = EcdsaSigner.from_private_key_file(k["ecdsa_priv"])
        verifier = EcdsaSigner.from_public_key_file(k["ecdsa_pub"])

        codec = CryptoCodec(
            cipher=AesGcmCipher(aes_key),
            signer=signer,
            verifier=verifier,
        )

        for msg_type in MessageType:
            enc_json = codec.encrypt(b"test", msg_type)
            stdout, stderr, rc = _cpp_tool(
                'decryptor',
                '--ecdsa-key', k["ecdsa_priv"],
                '--peer-pub', k["ecdsa_pub"],
                '--aes-key', k["aes_key_hex"],
                input_text=enc_json,
            )
            assert rc == 0, (
                f"C++ decryptor failed for {msg_type.value}:\n{stderr}"
            )
            assert "test" in stdout

    def test_tampered_envelope_rejected_by_cpp(self, cpp_keys):
        """C++ decryptor rejects tampered envelope."""
        k = cpp_keys
        aes_key = b64.hex_decode(k["aes_key_hex"])
        signer = EcdsaSigner.from_private_key_file(k["ecdsa_priv"])
        verifier = EcdsaSigner.from_public_key_file(k["ecdsa_pub"])

        codec = CryptoCodec(
            cipher=AesGcmCipher(aes_key),
            signer=signer,
            verifier=verifier,
        )

        enc_json = codec.encrypt(b"tampered test", MessageType.BUILD_TRIGGER)
        j = json.loads(enc_json)

        # Flip bits in ciphertext
        ct = b64.b64_decode(j["ciphertext"])
        ct_tampered = bytearray(ct)
        ct_tampered[0] ^= 0xFF
        j["ciphertext"] = b64.b64_encode(bytes(ct_tampered))
        tampered_json = json.dumps(j)

        stdout, stderr, rc = _cpp_tool(
            'decryptor',
            '--ecdsa-key', k["ecdsa_priv"],
            '--peer-pub', k["ecdsa_pub"],
            '--aes-key', k["aes_key_hex"],
            input_text=tampered_json,
        )

        assert rc != 0, "C++ decryptor should reject tampered envelope"
