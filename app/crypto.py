import base64
import hashlib
import json
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# 與前端共享的 passphrase；由它經 SHA-256 導出 256-bit AES 金鑰。
# 兩端的 passphrase 必須一致。正式環境請以環境變數覆寫。
AES_PASSPHRASE = os.environ.get("AES_PASSPHRASE", "land-info-dev-passphrase")

IV_LEN = 12  # AES-GCM nonce 長度 (bytes)，需與前端一致


def _aes_key() -> bytes:
    return hashlib.sha256(AES_PASSPHRASE.encode("utf-8")).digest()


def _decrypt_raw(raw: bytes) -> bytes:
    """解密 iv(12 bytes) ‖ ciphertext+tag 的位元組，回傳明文 bytes。"""
    if len(raw) <= IV_LEN:
        raise ValueError("密文長度不足（缺少 iv 或密文）")
    iv, ciphertext = raw[:IV_LEN], raw[IV_LEN:]
    try:
        return AESGCM(_aes_key()).decrypt(iv, ciphertext, None)
    except Exception as e:
        # 金鑰不符 / 內容被竄改 / tag 驗證失敗都會到這
        raise ValueError(f"AES 解密失敗: {e}")


def decrypt_file_bytes(raw: bytes) -> bytes:
    """解密 multipart 上傳的二進位密文（iv ‖ ciphertext+tag），回傳原始檔案 bytes。"""
    return _decrypt_raw(raw)


def decrypt_json(blob_b64: str) -> Any:
    """解密前端送來的 AES-GCM 密文，回傳原始 JSON 物件。

    前端格式：base64( iv(12 bytes) ‖ ciphertext+tag )，明文為 JSON 字串。
    解密或 JSON 解析失敗時拋出 ValueError。
    """
    try:
        raw = base64.b64decode(blob_b64)
    except Exception as e:
        raise ValueError(f"data_enc 不是合法 base64: {e}")

    plaintext = _decrypt_raw(raw)
    try:
        return json.loads(plaintext)
    except Exception as e:
        raise ValueError(f"解密後不是合法 JSON: {e}")
