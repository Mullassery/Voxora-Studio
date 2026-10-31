from __future__ import annotations

import pytest

from voxora.security import SecretBox


def test_encrypts_and_decrypts() -> None:
    box = SecretBox("x" * 40)
    ciphertext = box.encrypt("api_123")
    assert ciphertext.startswith("v1:")
    assert "api_123" not in ciphertext
    assert box.decrypt(ciphertext) == "api_123"


def test_wrong_key_rejected() -> None:
    box = SecretBox("a" * 40)
    other = SecretBox("b" * 40)
    ciphertext = box.encrypt("api_123")
    with pytest.raises(ValueError):
        other.decrypt(ciphertext)
