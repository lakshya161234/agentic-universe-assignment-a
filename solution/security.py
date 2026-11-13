"""Small local key wrapper: deterministic HMAC matching and AES-GCM PII storage."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class PIIProtection:
    def __init__(self, master_key: bytes):
        if len(master_key) < 32:
            raise ValueError("LEDGER_KEY must decode to at least 32 bytes")
        self._lookup_key = hmac.new(master_key, b"assignment-a/lookup/v1", hashlib.sha256).digest()
        self._encryption_key = hmac.new(master_key, b"assignment-a/encryption/v1", hashlib.sha256).digest()

    @classmethod
    def from_env(cls) -> "PIIProtection":
        value = os.environ.get("LEDGER_KEY", "").strip()
        if not value:
            raise ValueError("Set LEDGER_KEY to a base64-encoded secret before running the pipeline")
        try:
            key = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        except Exception as exc:
            raise ValueError("LEDGER_KEY must be valid URL-safe base64") from exc
        return cls(key)

    def fingerprint(self, normalized_phone: str) -> str:
        return hmac.new(self._lookup_key, normalized_phone.encode("utf-8"), hashlib.sha256).hexdigest()

    def encrypt(self, plaintext: str) -> bytes:
        nonce = os.urandom(12)
        return nonce + AESGCM(self._encryption_key).encrypt(nonce, plaintext.encode("utf-8"), None)

    def decrypt(self, ciphertext: bytes) -> str:
        nonce, encrypted = ciphertext[:12], ciphertext[12:]
        return AESGCM(self._encryption_key).decrypt(nonce, encrypted, None).decode("utf-8")
