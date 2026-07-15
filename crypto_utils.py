from __future__ import annotations

import base64
import hashlib
import sys

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator


ENCRYPTED_PREFIX = "enc$"
SENTINEL_VALUE = "__ul_encryption_key_valid__"


def _make_key(secret_key: str) -> bytes:
    return base64.urlsafe_b64encode(hashlib.sha256(secret_key.encode()).digest())


def encrypt(plaintext: str, secret_key: str) -> str:
    if not plaintext:
        return plaintext
    return ENCRYPTED_PREFIX + Fernet(_make_key(secret_key)).encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str, secret_key: str) -> str:
    if not ciphertext:
        return ciphertext
    if ciphertext.startswith(ENCRYPTED_PREFIX):
        return Fernet(_make_key(secret_key)).decrypt(ciphertext[len(ENCRYPTED_PREFIX):].encode()).decode()
    return ciphertext


class EncryptedText(TypeDecorator):
    impl = Text
    cache_ok = True

    def __init__(self, secret_key: str, *args, **kwargs):
        self.secret_key = secret_key
        super().__init__(*args, **kwargs)

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return encrypt(value, self.secret_key)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return decrypt(value, self.secret_key)


def verify_or_seal(app, db) -> None:
    from sqlalchemy import text

    secret = app.config["SECRET_KEY"]
    with app.app_context():
        existing = db.session.execute(
            text("SELECT value FROM encryption_sentinel WHERE id = 1")
        ).scalar()

        if existing:
            try:
                decrypt(existing, secret)
            except InvalidToken:
                print("FATAL: SECRET_KEY changed since data was encrypted.", file=sys.stderr)
                print("       Cannot decrypt encrypted columns.", file=sys.stderr)
                print("       Recover your old SECRET_KEY or set ENCRYPTION_RESET=1 to wipe encrypted data.", file=sys.stderr)
                sys.exit(1)
        else:
            sealed = encrypt(SENTINEL_VALUE, secret)
            db.session.execute(
                text("INSERT INTO encryption_sentinel (id, value) VALUES (1, :v)"),
                {"v": sealed},
            )
            db.session.commit()
