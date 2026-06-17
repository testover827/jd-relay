"""CryptoService 单元测试

覆盖:
- AES-256-GCM 加密/解密
- HMAC-SHA256 签名/验证
- 配置值加/解密 (PBKDF2)
- 无签名解密（钉钉回调场景）
- 边界情况（空输入、篡改检测等）
"""

import pytest

__import__("sys").path.insert(0, "/workspace/jenkins-dingtalk-relay/server")

from app.services.crypto_service import CryptoService, SecureConfig, SecurityError


# 固定测试密钥（保证确定性）
TEST_AES_KEY = "a" * 64       # hex-encoded 32 bytes
TEST_HMAC_SECRET = "b" * 64    # hex-encoded 32 bytes
TEST_MASTER_KEY = "c" * 64     # hex-encoded 32 bytes


@pytest.fixture
def crypto():
    return CryptoService(TEST_AES_KEY, TEST_HMAC_SECRET)


# ═══════════════════════════════════════════
# 加密功能
# ═══════════════════════════════════════════

class TestEncrypt:
    def test_encrypt_string(self, crypto):
        result = crypto.encrypt("Hello, World!")
        assert "ciphertext" in result
        assert "nonce" in result
        assert "signature" in result
        assert result["ciphertext"] != "Hello, World!"

    def test_encrypt_unicode(self, crypto):
        plaintext = "你好世界 🌍 测试"
        result = crypto.encrypt(plaintext)
        dec = crypto.decrypt(result["ciphertext"], result["nonce"], result["signature"])
        assert dec == plaintext

    def test_encrypt_empty(self, crypto):
        result = crypto.encrypt("")
        assert "ciphertext" in result

    def test_encrypt_json_roundtrip(self, crypto):
        data = {"key": "value", "num": 42}
        enc = crypto.encrypt_json(data)
        dec = crypto.decrypt_json(enc["ciphertext"], enc["nonce"], enc["signature"])
        assert dec == data

    def test_different_plaintexts(self, crypto):
        r1 = crypto.encrypt("hello")
        r2 = crypto.encrypt("world")
        assert r1["ciphertext"] != r2["ciphertext"]

    def test_same_plaintext_different_ct(self, crypto):
        """GCM 随机 nonce → 每次加密结果不同（语义安全）"""
        r1 = crypto.encrypt("same text")
        r2 = crypto.encrypt("same text")
        assert r1["nonce"] != r2["nonce"]
        assert r1["ciphertext"] != r2["ciphertext"]

    def test_special_characters(self, crypto):
        for text in [
            '{"json": "with\nnewlines"}',
            "path/to/file.tar.gz",
            "a=b&c=d",
            "line1\nline2\r\nline3",
        ]:
            enc = crypto.encrypt(text)
            dec = crypto.decrypt(enc["ciphertext"], enc["nonce"], enc["signature"])
            assert dec == text, f"Roundtrip failed: {text!r}"


# ═══════════════════════════════════════════
# 解密功能 + 安全性验证
# ═══════════════════════════════════════════

class TestDecrypt:
    def test_decrypt_valid(self, crypto):
        enc = crypto.encrypt("test message")
        assert crypto.decrypt(enc["ciphertext"], enc["nonce"], enc["signature"]) == "test message"

    def test_wrong_signature_rejected(self, crypto):
        enc = crypto.encrypt("secret")
        with pytest.raises(SecurityError, match="签名"):
            crypto.decrypt(enc["ciphertext"], enc["nonce"], "WRONG_SIGNATURE_VALUE!!!")

    def test_tampered_ciphertext_rejected(self, crypto):
        enc = crypto.encrypt("sensitive")
        # 翻转几个字节
        tampered = list(enc["ciphertext"])
        if len(tampered) > 10:
            tampered[5] = "X" if tampered[5] != "X" else "Y"
        with pytest.raises(Exception):
            crypto.decrypt("".join(tampered), enc["nonce"], enc["signature"])

    def test_wrong_nonce_fails(self, crypto):
        enc = crypto.encrypt("data")
        with pytest.raises(Exception):
            crypto.decrypt(enc["ciphertext"], "wrong_nonce_12345", enc["signature"])

    def test_empty_inputs_raise(self, crypto):
        with pytest.raises(Exception):
            crypto.decrypt("", "", "")

    def test_short_ciphertext_rejected(self, crypto):
        with pytest.raises(Exception):
            crypto.decrypt("abc", "n", "s")


# ═══════════════════════════════════════════
# 无签名解密（钉钉回调场景）
# ═══════════════════════════════════════════

class TestDecryptWithoutSignature:
    def test_without_signature(self, crypto):
        enc = crypto.encrypt("callback data")
        dec = crypto.decrypt_without_signature(enc["ciphertext"], enc["nonce"])
        assert dec == "callback data"

    def test_json_without_sig(self, crypto):
        data = {"processInstanceId": "pi_123", "result": "agree"}
        enc = crypto.encrypt_json(data)
        dec = crypto.decrypt_json_without_sig(enc["ciphertext"], enc["nonce"])
        assert dec == data


# ═══════════════════════════════════════════
# SecureConfig — PBKDF2 配置值加密
# ═══════════════════════════════════════════

class TestSecureConfig:
    def test_encrypt_decrypt_config(self):
        original = "my-super-secret-password"
        enc = SecureConfig.encrypt_config_value(original, TEST_MASTER_KEY)
        assert enc != original
        dec = SecureConfig.decrypt_config_value(enc, TEST_MASTER_KEY)
        assert dec == original

    def test_wrong_key_fails(self):
        enc = SecureConfig.encrypt_config_value("secret", TEST_MASTER_KEY)
        with pytest.raises(Exception):
            SecureConfig.decrypt_config_value(enc, "x" * 64)

    def test_empty_value(self):
        enc = SecureConfig.encrypt_config_value("", TEST_MASTER_KEY)
        assert SecureConfig.decrypt_config_value(enc, TEST_MASTER_KEY) == ""

    def test_long_value(self):
        original = "A" * 10000
        enc = SecureConfig.encrypt_config_value(original, TEST_MASTER_KEY)
        assert SecureConfig.decrypt_config_value(enc, TEST_MASTER_KEY) == original
