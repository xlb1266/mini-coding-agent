"""SecretBox 加解密单元测试。"""

from pathlib import Path

import pytest

from app.core.secret_box import SecretBox, SecretBoxError


@pytest.fixture()
def key_path(tmp_path: Path) -> Path:
    return tmp_path / "secret.key"


def test_master_key_file_created_once(key_path: Path) -> None:
    box = SecretBox(key_path)
    assert key_path.exists()

    first = key_path.read_text(encoding="ascii").strip()
    # 第二个实例复用同一把主密钥，历史密文仍可解
    SecretBox(key_path)
    assert key_path.read_text(encoding="ascii").strip() == first
    nonce, blob = box.encrypt("sk-abc")
    assert SecretBox(key_path).decrypt(nonce, blob) == "sk-abc"


def test_encrypt_roundtrip(key_path: Path) -> None:
    box = SecretBox(key_path)
    for plaintext in ("sk-plain", "包含中文的密钥", "a" * 500):
        nonce, blob = box.encrypt(plaintext)
        assert plaintext not in blob
        assert plaintext not in nonce
        assert box.decrypt(nonce, blob) == plaintext


def test_ciphertext_differs_for_same_plaintext(key_path: Path) -> None:
    box = SecretBox(key_path)
    nonce1, blob1 = box.encrypt("sk-same")
    nonce2, blob2 = box.encrypt("sk-same")
    assert (nonce1, blob1) != (nonce2, blob2)


def test_tampered_blob_rejected(key_path: Path) -> None:
    box = SecretBox(key_path)
    nonce, blob = box.encrypt("sk-abc")
    tampered = blob[:-4] + ("0000" if blob[-4:] != "0000" else "1111")
    with pytest.raises(SecretBoxError):
        box.decrypt(nonce, tampered)


def test_wrong_master_key_rejected(key_path: Path, tmp_path: Path) -> None:
    box = SecretBox(key_path)
    nonce, blob = box.encrypt("sk-abc")
    other = SecretBox(tmp_path / "other.key")
    with pytest.raises(SecretBoxError):
        other.decrypt(nonce, blob)


def test_malformed_input_rejected(key_path: Path) -> None:
    box = SecretBox(key_path)
    with pytest.raises(SecretBoxError):
        box.decrypt("not-hex", "zz")
    nonce, blob = box.encrypt("sk-abc")
    with pytest.raises(SecretBoxError):
        box.decrypt(nonce, "abcd")  # blob 短于标签长度
