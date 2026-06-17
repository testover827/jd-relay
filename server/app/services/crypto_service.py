"""AES-256-GCM 加密 + HMAC-SHA256 签名服务

加密流程：原始数据 → AES-256-GCM → Base64(ciphertext+tag) → HMAC 签名
解密流程：验证 HMAC → Base64 解码 → 分离 tag → AES-GCM 解密 → 返回明文

安全保证:
- 机密性: AES-256-GCM (Galois/Counter Mode)
- 完整性: GCM 认证 tag (16 bytes) + HMAC-SHA256 签名
"""

import os
import json
import hmac as _hmac
import hashlib
import base64
import logging

from Crypto.Cipher import AES

logger = logging.getLogger(__name__)


class SecurityError(Exception):
    """安全相关异常"""
    pass


class CryptoService:
    """提供应用层加解密与签名验证能力"""

    def __init__(self, aes_key_hex: str, hmac_secret_hex: str):
        if not aes_key_hex or not hmac_secret_hex:
            raise ValueError("AES key and HMAC secret are required")
        self.aes_key = bytes.fromhex(aes_key_hex)
        self.hmac_secret = bytes.fromhex(hmac_secret_hex)
        if len(self.aes_key) != 32:
            raise ValueError(f"AES key must be 32 bytes, got {len(self.aes_key)}")
        if len(self.hmac_secret) < 16:
            raise ValueError("HMAC secret must be at least 16 bytes")

    # ── 加解密 ──────────────────────────────

    def encrypt(self, plaintext: str) -> dict:
        """加密纯文本，返回 {ciphertext, nonce, signature}

        Args:
            plaintext: 待加密的 UTF-8 字符串

        Returns:
            包含 ciphertext(base64), nonce(base64), signature 的字典
        """
        nonce = os.urandom(12)
        cipher = AES.new(self.aes_key, AES.MODE_GCM, nonce=nonce)
        ciphertext, tag = cipher.encrypt_and_digest(plaintext.encode("utf-8"))
        payload = base64.b64encode(ciphertext + tag).decode()
        signature = self._sign(payload)
        return {
            "ciphertext": payload,
            "nonce": base64.b64encode(nonce).decode(),
            "signature": signature,
        }

    def decrypt(self, ciphertext: str, nonce: str, signature: str) -> str:
        """解密，签名不匹配时抛出 SecurityError

        Args:
            ciphertext: Base64 编码的密文（含 tag）
            nonce: Base64 编码的 nonce
            signature: HMAC-SHA256 签名

        Raises:
            SecurityError: 签名验证失败或解密失败
        """
        if not self._verify(ciphertext, signature):
            logger.warning("HMAC 签名验证失败 — 数据可能被篡改")
            raise SecurityError("HMAC 签名验证失败")

        raw = base64.b64decode(ciphertext)
        if len(raw) < 16:
            raise SecurityError("密文数据过短，无法解密")

        ciphertext_bytes, tag = raw[:-16], raw[-16:]
        nonce_bytes = base64.b64decode(nonce)

        cipher = AES.new(self.aes_key, AES.MODE_GCM, nonce=nonce_bytes)
        try:
            plaintext = cipher.decrypt_and_verify(ciphertext_bytes, tag)
        except (ValueError, KeyError) as e:
            logger.warning("AES-GCM 解密失败: %s", e)
            raise SecurityError(f"AES-GCM 解密失败: {e}")

        return plaintext.decode("utf-8")

    def decrypt_without_signature(self, ciphertext: str, nonce: str) -> str:
        """仅解密不做 HMAC 签名验证（适用于由其他机制保护的场景）

        Args:
            ciphertext: Base64 编码的密文（含 tag）
            nonce: Base64 编码的 nonce

        Note:
            此方法仍会执行 AES-GCM 内置的完整性校验（tag 验证），
            仅跳过外层 HMAC 签名。仅在确认数据来源可信时使用。
        """
        raw = base64.b64decode(ciphertext)
        if len(raw) < 16:
            raise SecurityError("密文数据过短，无法解密")

        ciphertext_bytes, tag = raw[:-16], raw[-16:]
        nonce_bytes = base64.b64decode(nonce)

        cipher = AES.new(self.aes_key, AES.MODE_GCM, nonce=nonce_bytes)
        try:
            plaintext = cipher.decrypt_and_verify(ciphertext_bytes, tag)
        except (ValueError, KeyError) as e:
            logger.warning("AES-GCM 解密失败: %s", e)
            raise SecurityError(f"AES-GCM 解密失败: {e}")

        return plaintext.decode("utf-8")

    def encrypt_json(self, data: dict) -> dict:
        """便捷方法：加密 JSON 对象"""
        return self.encrypt(json.dumps(data, ensure_ascii=False))

    def decrypt_json(self, ciphertext: str, nonce: str, signature: str) -> dict:
        """便捷方法：解密得到 JSON 对象"""
        plaintext = self.decrypt(ciphertext, nonce, signature)
        return json.loads(plaintext)

    def decrypt_json_without_sig(self, ciphertext: str, nonce: str) -> dict:
        """便捷方法：无签名验证解密 JSON 对象"""
        plaintext = self.decrypt_without_signature(ciphertext, nonce)
        return json.loads(plaintext)

    # ── 签名 ────────────────────────────────

    def _sign(self, data: str) -> str:
        return _hmac.new(self.hmac_secret, data.encode(), hashlib.sha256).hexdigest()

    def _verify(self, data: str, signature: str) -> bool:
        return _hmac.compare_digest(self._sign(data), signature)


# ── 配置值加密（用于 config 表敏感字段） ──

class SecureConfig:
    """使用 PBKDF2 派生密钥二次加密 config 表中的敏感值

    存储格式: base64(salt(16) + nonce(16) + tag(16) + ciphertext)
    """

    @staticmethod
    def encrypt_config_value(plaintext: str, master_key_hex: str) -> str:
        """使用 CONFIG_MASTER_KEY 加密配置值"""
        master_key = bytes.fromhex(master_key_hex)
        salt = os.urandom(16)
        key = hashlib.pbkdf2_hmac("sha256", master_key, salt, 100_000, dklen=32)
        cipher = AES.new(key, AES.MODE_GCM)
        ciphertext, tag = cipher.encrypt_and_digest(plaintext.encode("utf-8"))
        return base64.b64encode(salt + cipher.nonce + tag + ciphertext).decode()

    @staticmethod
    def decrypt_config_value(encrypted: str, master_key_hex: str) -> str:
        """解密配置值"""
        master_key = bytes.fromhex(master_key_hex)
        raw = base64.b64decode(encrypted)
        salt, nonce, tag, ciphertext = raw[:16], raw[16:32], raw[32:48], raw[48:]
        key = hashlib.pbkdf2_hmac("sha256", master_key, salt, 100_000, dklen=32)
        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        return cipher.decrypt_and_verify(ciphertext, tag).decode("utf-8")
