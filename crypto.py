"""
crypto.py — 전송 구간 AES 암호화

원본 ASMConsole 의 Utils.AESCrypto(AES + salt 기반 키 파생, Encrypt/Decrypt,
EncryptStringForJava 등)를 모사한 테스트용 구현.

  - AES-256-CBC + PKCS7 패딩
  - 키 파생: PBKDF2-HMAC-SHA256 (원본 .NET 의 Rfc2898DeriveBytes 대응)
  - 메시지 포맷: salt(16) | iv(16) | ciphertext   (앞에 salt/iv 를 붙여 전송)

실제 제품의 정확한 키/라운드/모드와 일치시키는 것이 목적이 아니라,
"전송 구간을 AESCrypto 스타일로 암호화한다"는 구조를 재현하는 데 목적이 있음.
"""

from __future__ import annotations

import os

from cryptography.hazmat.primitives import hashes, padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

PBKDF2_ITERS = 100_000
KEY_LEN = 32      # AES-256
SALT_LEN = 16
IV_LEN = 16


def _derive(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=KEY_LEN,
                     salt=salt, iterations=PBKDF2_ITERS)
    return kdf.derive(password.encode("utf-8"))


class AesCipher:
    """비밀번호(공유키 또는 AuthKey)로 바이트를 AES-256-CBC 암·복호화."""

    def __init__(self, password: str):
        self.password = password

    def encrypt(self, plaintext: bytes) -> bytes:
        salt = os.urandom(SALT_LEN)
        iv = os.urandom(IV_LEN)
        key = _derive(self.password, salt)
        padder = padding.PKCS7(algorithms.AES.block_size).padder()
        padded = padder.update(plaintext) + padder.finalize()
        enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        ct = enc.update(padded) + enc.finalize()
        return salt + iv + ct

    def decrypt(self, blob: bytes) -> bytes:
        salt, iv, ct = blob[:SALT_LEN], blob[SALT_LEN:SALT_LEN + IV_LEN], blob[SALT_LEN + IV_LEN:]
        key = _derive(self.password, salt)
        dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        padded = dec.update(ct) + dec.finalize()
        unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
        return unpadder.update(padded) + unpadder.finalize()
