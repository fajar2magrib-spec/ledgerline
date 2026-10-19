"""Custom SQLAlchemy column types.

* ``ScaledDecimal`` / ``MONEY``: exact decimals stored as integers (cents). Floats
  never touch money, SQLite's weak decimal handling is avoided, and the same schema
  behaves identically on PostgreSQL and MySQL.
* ``EncryptedString``: transparent Fernet encryption for personal data (client email,
  phone, tax id) when ENCRYPTION_KEY is configured.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from typing import Any, Optional

from sqlalchemy import BigInteger, Text
from sqlalchemy.types import TypeDecorator

from .config import ConfigError, get_config


class ScaledDecimal(TypeDecorator):
    """Decimal with a fixed number of places, persisted as an integer."""

    impl = BigInteger
    cache_ok = True

    def __init__(self, scale: int = 2) -> None:
        super().__init__()
        self.scale = scale

    def process_bind_param(self, value: Any, dialect: Any) -> Optional[int]:
        if value is None:
            return None
        quantum = Decimal(1).scaleb(-self.scale)
        scaled = Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP)
        return int(scaled.scaleb(self.scale))

    def process_result_value(self, value: Any, dialect: Any) -> Optional[Decimal]:
        return None if value is None else Decimal(int(value)).scaleb(-self.scale)


MONEY = ScaledDecimal(2)

ENC_PREFIX = "enc:v1:"


@lru_cache(maxsize=4)
def _fernet(key: str):
    try:
        from cryptography.fernet import Fernet

        return Fernet(key.encode())
    except ImportError as exc:  # pragma: no cover
        raise ConfigError("ENCRYPTION_KEY is set but 'cryptography' is not installed.") from exc
    except ValueError as exc:
        raise ConfigError(
            "ENCRYPTION_KEY is not a valid Fernet key. Generate one with: python -c "
            '"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        ) from exc


class EncryptedString(TypeDecorator):
    """Encrypts on write when a key exists; always decrypts values it finds marked.

    Values carry an ``enc:v1:`` prefix, so plaintext rows written before a key was
    configured still read fine and are encrypted the next time they are saved.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Optional[str]:
        key = get_config().encryption_key
        if value in (None, "") or not key:
            return value
        return ENC_PREFIX + _fernet(key).encrypt(str(value).encode()).decode()

    def process_result_value(self, value: Any, dialect: Any) -> Optional[str]:
        if not value or not str(value).startswith(ENC_PREFIX):
            return value
        key = get_config().encryption_key
        if not key:
            raise ConfigError("Encrypted data found but ENCRYPTION_KEY is not configured.")
        return _fernet(key).decrypt(str(value)[len(ENC_PREFIX):].encode()).decode()
