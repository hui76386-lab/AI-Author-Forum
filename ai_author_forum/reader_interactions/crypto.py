"""Reader identity cryptography and normalization primitives."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email


class ReaderCryptoError(ValueError):
    pass


def normalize_email(value: str) -> str:
    candidate = str(value or "").strip()
    if len(candidate) > 254 or candidate.count("@") != 1:
        raise ValidationError("Invalid email address.")
    local, domain = candidate.rsplit("@", 1)
    if not local or not domain:
        raise ValidationError("Invalid email address.")
    try:
        normalized_domain = domain.lower().encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValidationError("Invalid email address.") from exc
    normalized = f"{local}@{normalized_domain}"
    validate_email(normalized)
    return normalized


def keyed_digest(value: str, key: str) -> str:
    return hmac.new(key.encode(), value.encode(), hashlib.sha256).hexdigest()


def token_digest(token: str) -> str:
    return keyed_digest(token, settings.READER_TOKEN_PEPPER)


def email_lookup_digest(email: str) -> str:
    return keyed_digest(normalize_email(email), settings.READER_EMAIL_LOOKUP_KEY)


def security_fingerprint(*parts: str) -> str:
    value = "\0".join(str(part or "")[:512] for part in parts)
    return keyed_digest(value, settings.READER_TOKEN_PEPPER)


@dataclass(frozen=True)
class ProtectedValue:
    ciphertext: str
    key_version: int


class EmailProtector:
    def __init__(self, key_spec: str):
        versioned_keys = []
        seen_versions = set()
        for entry in str(key_spec or "").split(","):
            try:
                version_text, encoded_key = entry.strip().split(":", 1)
                version = int(version_text)
                fernet = Fernet(encoded_key.encode())
            except (TypeError, ValueError) as exc:
                raise ReaderCryptoError(
                    "Invalid reader encryption key configuration."
                ) from exc
            if version < 1 or version in seen_versions:
                raise ReaderCryptoError("Invalid reader encryption key version.")
            seen_versions.add(version)
            versioned_keys.append((version, fernet))
        if not versioned_keys:
            raise ReaderCryptoError("At least one reader encryption key is required.")
        self._versioned_keys = tuple(versioned_keys)
        self._multi = MultiFernet([fernet for _, fernet in versioned_keys])

    @classmethod
    def from_settings(cls):
        return cls(settings.READER_EMAIL_ENCRYPTION_KEYS)

    @property
    def primary_version(self):
        return self._versioned_keys[0][0]

    def encrypt_text(self, value: str) -> ProtectedValue:
        ciphertext = self._multi.encrypt(value.encode()).decode()
        return ProtectedValue(ciphertext, self.primary_version)

    def decrypt_text(self, ciphertext: str) -> str:
        try:
            return self._multi.decrypt(ciphertext.encode()).decode()
        except (InvalidToken, UnicodeDecodeError) as exc:
            raise ReaderCryptoError(
                "Protected reader value cannot be decrypted."
            ) from exc
