"""本地密钥加密盒：API Key 经认证加密后才能落库。

方案（仅用标准库，不引入新依赖）：
- 主密钥为 32 字节随机数，保存在本地文件（默认 backend/secret.key，不入版本库）；
- 加密使用 HMAC-SHA256 计数器模式的密钥流做异或（流密码），再对 密文+nonce
  计算 HMAC-SHA256 标签（encrypt-then-MAC），篡改或换钥都会在校验时失败。
接口层只保存/读取 nonce 与密文，任何地方不出现明文密钥。
"""

import hmac
import hashlib
import os
from pathlib import Path

_BLOCK_SIZE = 32  # 每块密钥流 32 字节（SHA-256 输出长度）
_NONCE_SIZE = 16
_TAG_SIZE = 32
_MASTER_KEY_SIZE = 32

_BLOB_FORMAT_ERROR = "密钥数据格式不正确，无法解密"
_TAMPER_ERROR = "密钥数据校验失败，可能已损坏或主密钥不匹配"


def _keystream(master_key: bytes, nonce: bytes, length: int) -> bytes:
    """生成 length 字节的密钥流：HMAC(key, nonce || 计数器) 逐块拼接。"""
    blocks: list[bytes] = []
    counter = 0
    while sum(len(b) for b in blocks) < length:
        blocks.append(hmac.new(master_key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest())
        counter += 1
    return b"".join(blocks)[:length]


def _xor(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


class SecretBoxError(Exception):
    """密钥盒操作失败（数据损坏或主密钥不匹配）。"""


class SecretBox:
    """以本地主密钥文件为根的对称加解密盒。"""

    def __init__(self, key_path: Path) -> None:
        self._key = self._load_or_create_master_key(Path(key_path))

    @staticmethod
    def _load_or_create_master_key(key_path: Path) -> bytes:
        """首次使用时生成主密钥文件；之后一直复用同一把钥匙。"""
        if key_path.exists():
            content = key_path.read_text(encoding="ascii").strip()
            try:
                key = bytes.fromhex(content)
            except ValueError as exc:
                raise SecretBoxError(f"主密钥文件格式不正确：{key_path}") from exc
            if len(key) != _MASTER_KEY_SIZE:
                raise SecretBoxError(f"主密钥文件长度不正确：{key_path}")
            return key

        key = os.urandom(_MASTER_KEY_SIZE)
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_text(key.hex(), encoding="ascii")
        try:
            key_path.chmod(0o600)  # POSIX 下限制读取权限；Windows 忽略失败
        except OSError:
            pass
        return key

    def encrypt(self, plaintext: str) -> tuple[str, str]:
        """加密明文，返回 (nonce_hex, blob_hex)；blob = HMAC 标签 + 密文。"""
        data = plaintext.encode("utf-8")
        nonce = os.urandom(_NONCE_SIZE)
        ciphertext = _xor(data, _keystream(self._key, nonce, len(data)))
        tag = hmac.new(self._key, nonce + ciphertext, hashlib.sha256).digest()
        return nonce.hex(), (tag + ciphertext).hex()

    def decrypt(self, nonce_hex: str, blob_hex: str) -> str:
        """解密 (nonce_hex, blob_hex)，校验失败或格式错误时抛出 SecretBoxError。"""
        try:
            nonce = bytes.fromhex(nonce_hex)
            blob = bytes.fromhex(blob_hex)
        except ValueError as exc:
            raise SecretBoxError(_BLOB_FORMAT_ERROR) from exc
        if len(nonce) != _NONCE_SIZE or len(blob) < _TAG_SIZE:
            raise SecretBoxError(_BLOB_FORMAT_ERROR)

        tag, ciphertext = blob[:_TAG_SIZE], blob[_TAG_SIZE:]
        expected = hmac.new(self._key, nonce + ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected):
            raise SecretBoxError(_TAMPER_ERROR)
        return _xor(ciphertext, _keystream(self._key, nonce, len(ciphertext))).decode("utf-8")


_box: SecretBox | None = None


def get_secret_box() -> SecretBox:
    """FastAPI 依赖：进程内共享一个 SecretBox（懒加载，避免导入时创建密钥文件）。"""
    global _box
    if _box is None:
        from app.core.config import settings

        _box = SecretBox(settings.master_key_path)
    return _box
