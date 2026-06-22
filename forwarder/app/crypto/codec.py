"""CryptoCodec — top-level encrypt/decrypt with full validation pipeline.

Wire-compatible with C++ CryptoCodec.
Encrypt: plaintext → metadata → AES-GCM → ECDSA sign → CryptoEnvelope JSON
Decrypt: CryptoEnvelope JSON → timestamp check → replay check → ECDSA verify → AES-GCM decrypt → plaintext
"""

import json
from dataclasses import dataclass

from .aes_gcm import AesGcmCipher, EncryptResult as _EncryptResult
from .ecdsa import EcdsaSigner
from .replay_guard import ReplayGuard
from .envelope import CryptoEnvelope, MessageType, build_signing_payload
from . import base64 as b64


@dataclass
class DecryptResult:
    """Result of a decrypt operation. Matches C++ DecryptResult semantics."""
    plaintext: bytes = b""
    ok: bool = False
    error: str = ""


class CryptoCodec:
    """High-level codec that ties together cipher + signer + verifier + replay guard.

    Provides one-call encrypt/decrypt producing/consuming CryptoEnvelope JSON.

    Usage:
        codec = CryptoCodec(
            cipher=AesGcmCipher(aes_key),
            signer=EcdsaSigner.from_private_key_file("my_priv.pem"),
            verifier=EcdsaSigner.from_public_key_file("peer_pub.pem"),
            guard=ReplayGuard(window_seconds=300),
        )
        # Encrypt
        json_str = codec.encrypt(b"hello", MessageType.BUILD_TRIGGER)
        # Decrypt
        result = codec.decrypt(json_str)
    """

    def __init__(
        self,
        cipher: AesGcmCipher,
        signer: EcdsaSigner,
        verifier: EcdsaSigner,
        guard: ReplayGuard | None = None,
    ):
        """Construct CryptoCodec.

        Args:
            cipher: AES-256-GCM cipher (shared session key).
            signer: ECDSA signer with OUR private key (for outgoing messages).
            verifier: ECDSA signer with PEER's public key (for incoming messages).
            guard: ReplayGuard (created with default ±5min window if None).
        """
        self._cipher = cipher
        self._signer = signer
        self._verifier = verifier
        self._guard = guard or ReplayGuard()

    # ── Encrypt ──────────────────────────────────────────────────

    def encrypt(self, plaintext: bytes, msg_type: MessageType) -> str:
        """Encrypt plaintext into a CryptoEnvelope JSON string.

        Pipeline:
        1. Generate metadata (msg_id, timestamp, nonce)
        2. AES-256-GCM encrypt plaintext → ciphertext + iv + tag
        3. Build signing payload → ECDSA sign
        4. Serialize to JSON

        Args:
            plaintext: Raw bytes to encrypt (typically UTF-8 JSON).
            msg_type: Message type enum.

        Returns:
            JSON string ready to send over WebSocket.
        """
        env = CryptoEnvelope()

        # 1. Generate metadata
        env.msg_id = b64.generate_uuid()
        env.timestamp = ReplayGuard.now_ms()
        env.nonce = b64.b64_encode(b64.random_bytes(16))
        env.type = msg_type.value

        # 2. AES-256-GCM encrypt
        enc_result = self._cipher.encrypt(plaintext)
        if not enc_result.ok:
            raise RuntimeError(f"Encryption failed: {enc_result.error}")

        env.iv = b64.b64_encode(enc_result.iv)
        env.ciphertext = b64.b64_encode(enc_result.ciphertext)
        env.tag = b64.b64_encode(enc_result.tag)

        # 3. ECDSA sign the canonical payload
        payload = build_signing_payload(env)
        signature = self._signer.sign(payload.encode("utf-8"))
        env.signature = b64.b64_encode(signature)

        # 4. Serialize to JSON
        return self.to_json(env)

    # ── Decrypt ──────────────────────────────────────────────────

    def decrypt(self, json_str: str) -> DecryptResult:
        """Decrypt a CryptoEnvelope JSON string with full validation.

        Pipeline (matches C++ exactly):
        1. Parse JSON → CryptoEnvelope
        2. Check timestamp window
        3. Check nonce replay
        4. Verify ECDSA signature
        5. AES-256-GCM decrypt
        6. Record nonce to prevent future replays

        Returns DecryptResult with .ok=True and .plaintext on success,
        or .ok=False and .error on any validation failure.
        """
        result = DecryptResult()

        # 1. Parse JSON
        env = self.from_json(json_str)

        # 2. Check timestamp window
        if not self._guard.is_within_window(env.timestamp):
            result.error = (
                f"Timestamp outside acceptable window (msg_id={env.msg_id})"
            )
            return result

        # 3. Check replay (nonce uniqueness)
        if self._guard.is_replay(env.nonce):
            result.error = (
                f"Replay detected: nonce already seen (msg_id={env.msg_id})"
            )
            return result

        # 4. Verify ECDSA signature
        payload = build_signing_payload(env)
        signature = b64.b64_decode(env.signature)
        if not self._verifier.verify(payload.encode("utf-8"), signature):
            result.error = (
                f"ECDSA signature verification failed (msg_id={env.msg_id})"
            )
            return result

        # 5. AES-256-GCM decrypt
        ciphertext = b64.b64_decode(env.ciphertext)
        iv = b64.b64_decode(env.iv)
        tag = b64.b64_decode(env.tag)

        dec_result = self._cipher.decrypt(ciphertext, iv, tag)
        if not dec_result.ok:
            result.error = (
                f"AES-GCM decryption failed: {dec_result.error} "
                f"(msg_id={env.msg_id})"
            )
            return result

        # 6. Record nonce to prevent future replays
        self._guard.record_nonce(env.nonce)

        result.plaintext = dec_result.plaintext
        result.ok = True
        return result

    # ── JSON serialization ───────────────────────────────────────

    @staticmethod
    def to_json(env: CryptoEnvelope) -> str:
        """Serialize CryptoEnvelope to JSON string.
        Field names match C++ CryptoCodec::to_json() exactly.
        Uses compact separators to match nlohmann::json::dump()."""
        return json.dumps({
            "msg_id": env.msg_id,
            "timestamp": env.timestamp,
            "nonce": env.nonce,
            "type": env.type,
            "iv": env.iv,
            "ciphertext": env.ciphertext,
            "tag": env.tag,
            "signature": env.signature,
        }, ensure_ascii=False, separators=(',', ':'))

    @staticmethod
    def from_json(json_str: str) -> CryptoEnvelope:
        """Parse JSON string to CryptoEnvelope.
        Matches C++ CryptoCodec::from_json() exactly."""
        j = json.loads(json_str)
        return CryptoEnvelope(
            msg_id=j.get("msg_id", ""),
            timestamp=j.get("timestamp", 0),
            nonce=j.get("nonce", ""),
            type=j.get("type", ""),
            iv=j.get("iv", ""),
            ciphertext=j.get("ciphertext", ""),
            tag=j.get("tag", ""),
            signature=j.get("signature", ""),
        )
