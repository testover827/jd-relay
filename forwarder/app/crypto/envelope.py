"""CryptoEnvelope and MessageType — wire-format identical to C++ jd_relay::crypto.

JSON field names: msg_id, timestamp, nonce, type, iv, ciphertext, tag, signature
Signing payload: msg_id|timestamp|nonce|type|iv|ciphertext|tag
"""

from dataclasses import dataclass, field
from enum import Enum


class MessageType(str, Enum):
    """Message types for the Forwarder ↔ Agent encrypted channel.
    Values match C++ to_string() output exactly."""
    BUILD_TRIGGER = "BUILD_TRIGGER"
    BUILD_RESULT = "BUILD_RESULT"
    SENSITIVE_REVIEW_REQ = "SENSITIVE_REVIEW_REQ"
    SECOND_REVIEW_RESULT = "SECOND_REVIEW_RESULT"
    HEARTBEAT = "HEARTBEAT"
    ACK = "ACK"

    @classmethod
    def parse(cls, s: str) -> "MessageType":
        """Parse from string. Raises ValueError on unknown type."""
        for member in cls:
            if member.value == s:
                return member
        raise ValueError(f"Unknown message type: {s}")


@dataclass
class CryptoEnvelope:
    """The encrypted envelope structure.
    Wire format is JSON with base64-encoded binary fields.
    Field semantics match C++ CryptoEnvelope exactly.
    """
    msg_id: str = ""          # UUID v4
    timestamp: int = 0        # Unix epoch milliseconds
    nonce: str = ""           # base64(16 random bytes)
    type: str = ""            # MessageType string
    iv: str = ""              # base64(12 bytes, AES-GCM nonce)
    ciphertext: str = ""      # base64(AES-256-GCM(plaintext))
    tag: str = ""             # base64(16 bytes, GCM auth tag)
    signature: str = ""       # base64(ECDSA signature over signing payload)

    @property
    def msg_type(self) -> MessageType:
        return MessageType.parse(self.type)


def build_signing_payload(env: CryptoEnvelope) -> str:
    """Build the canonical signing payload.
    Format: msg_id|timestamp|nonce|type|iv|ciphertext|tag
    Matches C++ CryptoEnvelope::build_signing_payload() exactly.
    """
    return "|".join([
        env.msg_id,
        str(env.timestamp),
        env.nonce,
        env.type,
        env.iv,
        env.ciphertext,
        env.tag,
    ])
