"""One-time process start-up (wrapped by ``st.cache_resource`` in app.py).

Streamlit re-executes app.py on every interaction, but a cached resource is created once
per server process, which is exactly what schema creation and the background worker need.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .config import configure_logging, get_config
from .db import database_info, init_schema
from .services import backup, outbox, users

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BootInfo:
    backend: str
    journal_mode: str
    admin_created_from_env: bool
    ephemeral_secret: bool


def start() -> BootInfo:
    configure_logging()
    cfg = get_config()
    init_schema()
    created = users.bootstrap_admin_from_config()
    outbox.start_worker()
    backup.start_worker()  # automatic, verified database backups (SQLite)
    info = database_info()
    log.info("Ledgerline started: %s (%s)", info["backend"], cfg.env)
    return BootInfo(info["backend"], info.get("journal_mode", "-"), created, cfg.auth_secret_is_ephemeral)
