"""Automatic database backups (SQLite).

Goal: a crash, a bad update or a deleted file must never cost the business its data.

* A consistent copy is made with SQLite's online-backup API, so it is safe while the app
  is running and includes everything still sitting in the WAL file.
* Every copy is verified (``PRAGMA integrity_check``) before it counts as a backup.
* Backups run once at start-up (if the newest one is older than the interval) and then in
  the background, default every 6 hours.
* Old copies are rotated: the newest ``BACKUP_KEEP`` are kept (default 60).
* Optional second location ``BACKUP_EXTRA_DIR`` (a USB drive, a Google Drive / OneDrive
  folder, a network share). A backup that lives only next to the database does not
  survive a dead disk, so set this one.

Settings (environment / .env): BACKUP_ENABLED, BACKUP_DIR, BACKUP_EXTRA_DIR,
BACKUP_INTERVAL_HOURS, BACKUP_KEEP.

PostgreSQL/MySQL are backed up with the database's own tools (pg_dump) or the hosting
provider's automatic backups; this module only handles SQLite and says so.

Restore: stop the app, copy the chosen ``ledgerline_YYYYmmdd_HHMMSS.db`` over
``data/ledgerline.db`` (delete any ``ledgerline.db-wal`` / ``-shm`` next to it), start the app.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)
PREFIX, SUFFIX = "ledgerline_", ".db"


class BackupError(RuntimeError):
    pass


@dataclass(frozen=True)
class BackupInfo:
    path: Path
    size: int
    created: datetime


# ------------------------------------------------------------------ core (paths only)
def backup_sqlite(source: Path, dest_dir: Path, now: Optional[datetime] = None) -> Path:
    """Copy ``source`` into ``dest_dir`` as a verified, timestamped file and return its path."""
    source, dest_dir = Path(source), Path(dest_dir)
    if not source.exists():
        raise BackupError(f"Database file not found: {source}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    final = dest_dir / f"{PREFIX}{stamp}{SUFFIX}"
    partial = dest_dir / f".{final.name}.partial"
    src = dst = None
    try:
        src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True, timeout=30)
        dst = sqlite3.connect(partial.as_posix())
        src.backup(dst)
        result = dst.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise BackupError(f"The new backup failed its integrity check: {result}")
        dst.close()
        dst = None
        partial.replace(final)  # appears under its real name only once it is complete and verified
    except sqlite3.Error as exc:
        raise BackupError(f"Backup failed: {exc}") from exc
    finally:
        for conn in (src, dst):
            if conn is not None:
                conn.close()
        partial.unlink(missing_ok=True)
    return final


def list_backups(directory: Path) -> list[BackupInfo]:
    """Backups in ``directory``, newest first."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    rows = []
    for p in directory.glob(f"{PREFIX}*{SUFFIX}"):
        st = p.stat()
        rows.append(BackupInfo(p, st.st_size, datetime.fromtimestamp(st.st_mtime)))
    return sorted(rows, key=lambda b: b.created, reverse=True)


def rotate(directory: Path, keep: int) -> int:
    """Delete all but the newest ``keep`` backups. Returns how many were removed."""
    old = list_backups(directory)[max(keep, 1):]
    for b in old:
        b.path.unlink(missing_ok=True)
    return len(old)


# ------------------------------------------------------------------ app integration
@dataclass(frozen=True)
class BackupSettings:
    enabled: bool
    directory: Path
    extra_directory: Optional[Path]
    interval_hours: float
    keep: int


def _settings() -> BackupSettings:
    from ..config import BASE_DIR, setting

    def num(name: str, default: float) -> float:
        try:
            return max(float(setting(name, str(default))), 0.1)  # type: ignore[arg-type]
        except ValueError:
            return default

    extra = setting("BACKUP_EXTRA_DIR")
    return BackupSettings(
        enabled=(setting("BACKUP_ENABLED", "true") or "true").strip().lower() in {"1", "true", "yes", "on"},
        directory=Path(setting("BACKUP_DIR", str(BASE_DIR / "data" / "backups"))).expanduser(),
        extra_directory=Path(extra).expanduser() if extra else None,
        interval_hours=num("BACKUP_INTERVAL_HOURS", 6),
        keep=int(num("BACKUP_KEEP", 60)),
    )


def sqlite_path() -> Optional[Path]:
    """The SQLite file in use, or None for PostgreSQL/MySQL/in-memory."""
    from sqlalchemy.engine import make_url

    from ..config import get_config

    url = make_url(get_config().database_url)
    if url.get_backend_name() != "sqlite" or url.database in (None, "", ":memory:"):
        return None
    return Path(url.database).expanduser()


def current_settings() -> BackupSettings:
    return _settings()


def run_backup() -> Path:
    """Make one backup now (plus a copy in the extra location) and rotate old ones."""
    cfg, db_file = _settings(), sqlite_path()
    if db_file is None:
        raise BackupError("Automatic backup supports SQLite. For PostgreSQL/MySQL use pg_dump or your host's backups.")
    path = backup_sqlite(db_file, cfg.directory)
    rotate(cfg.directory, cfg.keep)
    if cfg.extra_directory is not None:
        try:
            cfg.extra_directory.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, cfg.extra_directory / path.name)
            rotate(cfg.extra_directory, cfg.keep)
        except OSError as exc:  # a missing USB drive must not break the app or the local backup
            log.warning("Could not copy the backup to %s: %s", cfg.extra_directory, exc)
    log.info("Database backup written: %s", path)
    return path


def latest_backup() -> Optional[BackupInfo]:
    found = list_backups(_settings().directory)
    return found[0] if found else None


class BackupWorker(threading.Thread):
    def __init__(self) -> None:
        super().__init__(name="ledgerline-backup", daemon=True)

    def run(self) -> None:
        while True:
            try:
                cfg = _settings()
                if cfg.enabled and sqlite_path() is not None:
                    newest = latest_backup()
                    age_h = (datetime.now() - newest.created).total_seconds() / 3600 if newest else 1e9
                    if age_h >= cfg.interval_hours:
                        run_backup()
            except Exception:  # noqa: BLE001  (a failed backup must never take the app down)
                log.exception("Scheduled backup failed")
            time.sleep(600)  # re-check every 10 minutes


_worker: Optional[BackupWorker] = None
_lock = threading.Lock()


def start_worker() -> None:
    """Start the background backup thread for this process (idempotent)."""
    global _worker
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = BackupWorker()
            _worker.start()
