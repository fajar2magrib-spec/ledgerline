"""The one-click launcher: secrets are generated once and never overwrite what the user already has."""
import base64
import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("launch", Path(__file__).resolve().parent.parent / "launch.py")
launch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launch)


def _values(path: Path) -> dict:
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if "=" in line and not line.startswith("#"))


def test_creates_env_from_example_with_both_secrets(tmp_path):
    (tmp_path / ".env.example").write_text("# comment\nAUTH_SECRET=\nENCRYPTION_KEY=\nSESSION_DAYS=7\n", encoding="utf-8")
    assert launch.ensure_env(tmp_path) == ["AUTH_SECRET", "ENCRYPTION_KEY"]
    values = _values(tmp_path / ".env")
    assert len(values["AUTH_SECRET"]) >= 48 and values["SESSION_DAYS"] == "7"
    assert len(base64.urlsafe_b64decode(values["ENCRYPTION_KEY"])) == 32          # a valid Fernet key


def test_existing_values_are_never_overwritten_and_second_run_changes_nothing(tmp_path):
    (tmp_path / ".env").write_text("AUTH_SECRET=mine\nENCRYPTION_KEY=\n", encoding="utf-8")
    assert launch.ensure_env(tmp_path) == ["ENCRYPTION_KEY"]
    first = (tmp_path / ".env").read_text(encoding="utf-8")
    assert _values(tmp_path / ".env")["AUTH_SECRET"] == "mine"
    assert launch.ensure_env(tmp_path) == []
    assert (tmp_path / ".env").read_text(encoding="utf-8") == first


def test_works_without_an_example_file_and_ignores_commented_keys(tmp_path):
    (tmp_path / ".env.example").unlink() if (tmp_path / ".env.example").exists() else None
    (tmp_path / ".env").write_text("# AUTH_SECRET=old\n", encoding="utf-8")
    assert launch.ensure_env(tmp_path) == ["AUTH_SECRET", "ENCRYPTION_KEY"]


def test_reinstall_only_when_requirements_change(tmp_path):
    (tmp_path / "requirements.txt").write_text("streamlit\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    assert launch.needs_install(tmp_path)
    (tmp_path / ".venv" / ".requirements.sha256").write_text(launch.requirements_digest(tmp_path), encoding="utf-8")
    assert not launch.needs_install(tmp_path)
    (tmp_path / "requirements.txt").write_text("streamlit\npandas\n", encoding="utf-8")
    assert launch.needs_install(tmp_path)
