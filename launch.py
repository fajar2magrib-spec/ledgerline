"""Ledgerline launcher: prepares everything the first time, then starts the app.

Customers do not need to type commands: double-click ``start.bat`` (Windows),
``start.command`` (Mac) or ``start.sh`` (Linux). Developers can run ``python launch.py``.
Uses only the Python standard library, so it works before anything is installed.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MIN_PYTHON = (3, 10)

# Secrets generated once and kept in .env. ENCRYPTION_KEY is a Fernet key (32 random bytes, url-safe base64).
GENERATED = {
    "AUTH_SECRET": lambda: secrets.token_urlsafe(48),
    "ENCRYPTION_KEY": lambda: base64.urlsafe_b64encode(os.urandom(32)).decode("ascii"),
}


def say(message: str) -> None:
    print(f"\n=== {message}", flush=True)


def venv_python(root: Path = ROOT) -> Path:
    return root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ensure_env(root: Path = ROOT) -> list[str]:
    """Create ``.env`` (from ``.env.example``) and fill any blank secret. Existing values are never touched.

    Returns the names of the secrets that were generated this time.
    """
    env, example = root / ".env", root / ".env.example"
    if env.exists():
        lines = env.read_text(encoding="utf-8").splitlines()
    elif example.exists():
        lines = example.read_text(encoding="utf-8").splitlines()
    else:
        lines = ["LEDGERLINE_ENV=development"]
    created = []
    for key, make in GENERATED.items():
        index = next((i for i, line in enumerate(lines) if line.split("=", 1)[0].strip() == key), None)
        if index is None:
            lines.append(f"{key}={make()}")
            created.append(key)
        elif not lines[index].partition("=")[2].strip():
            lines[index] = f"{key}={make()}"
            created.append(key)
    if created or not env.exists():
        env.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return created


def requirements_digest(root: Path = ROOT) -> str:
    return hashlib.sha256((root / "requirements.txt").read_bytes()).hexdigest()


def needs_install(root: Path = ROOT) -> bool:
    marker = root / ".venv" / ".requirements.sha256"
    return not marker.exists() or marker.read_text(encoding="utf-8").strip() != requirements_digest(root)


def ensure_environment(root: Path = ROOT) -> Path:
    """Create the private Python environment and install the libraries when missing or out of date."""
    python = venv_python(root)
    if not python.exists():
        say("Preparing a private Python environment (first time only)")
        venv.create(root / ".venv", with_pip=True)
    if needs_install(root):
        say("Installing the libraries the app needs (first time only, about 2-5 minutes, needs internet)")
        subprocess.check_call([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r",
                               str(root / "requirements.txt")])
        (root / ".venv" / ".requirements.sha256").write_text(requirements_digest(root), encoding="utf-8")
    return python


def main() -> int:
    if sys.version_info < MIN_PYTHON:
        print(f"Ledgerline needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer. You have {sys.version.split()[0]}.\n"
              "Please install the latest Python from https://www.python.org/downloads/")
        return 1
    os.chdir(ROOT)
    created = ensure_env()
    if created:
        say("Created your private security keys in the file .env. Keep a copy of that file safe: "
            "without it, saved client details cannot be read.")
    try:
        python = ensure_environment()
    except subprocess.CalledProcessError:
        print("\nInstalling the libraries failed. Please check your internet connection and run this again.")
        return 1
    say("Starting Ledgerline. Your browser will open. Close this window to stop the app.")
    try:
        return subprocess.call([str(python), "-m", "streamlit", "run", "app.py", "--browser.gatherUsageStats=false"], cwd=ROOT)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
