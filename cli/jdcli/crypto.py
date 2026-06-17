"""CLI 工具 — 加解密模块（与服务端逻辑一致）"""

import os
import hmac
import hashlib
import base64
import json

from Crypto.Cipher import AES


class CLICrypto:
    """CLI 端加解密，与服务端 CryptoService 保持一致"""

    def __init__(self, aes_key_hex: str, hmac_secret_hex: str):
        self.aes_key = bytes.fromhex(aes_key_hex)
        self.hmac_secret = bytes.fromhex(hmac_secret_hex)

    def encrypt_json(self, data: dict) -> dict:
        """加密 JSON 数据"""
        nonce = os.urandom(12)
        cipher = AES.new(self.aes_key, AES.MODE_GCM, nonce=nonce)
        plaintext = json.dumps(data, ensure_ascii=False).encode("utf-8")
        ciphertext, tag = cipher.encrypt_and_digest(plaintext)
        payload = base64.b64encode(ciphertext + tag).decode()
        signature = self._sign(payload)
        return {
            "ciphertext": payload,
            "nonce": base64.b64encode(nonce).decode(),
            "signature": signature,
        }

    def decrypt_json(self, ciphertext: str, nonce: str, signature: str) -> dict:
        """解密得到 JSON"""
        if not self._verify(ciphertext, signature):
            raise ValueError("HMAC 签名验证失败")
        raw = base64.b64decode(ciphertext)
        ct_bytes, tag = raw[:-16], raw[-16:]
        cipher = AES.new(
            self.aes_key, AES.MODE_GCM,
            nonce=base64.b64decode(nonce)
        )
        plaintext = cipher.decrypt_and_verify(ct_bytes, tag)
        return json.loads(plaintext.decode("utf-8"))

    def _sign(self, data: str) -> str:
        return hmac.new(self.hmac_secret, data.encode(), hashlib.sha256).hexdigest()

    def _verify(self, data: str, signature: str) -> bool:
        return hmac.compare_digest(self._sign(data), signature)
