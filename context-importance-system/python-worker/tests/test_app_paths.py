"""Where user data lives: legacy layout from source, a data folder when told to or when frozen."""

from __future__ import annotations

import sys
from pathlib import Path

import app_paths


def test_from_source_the_legacy_layout_is_kept(monkeypatch):
    monkeypatch.delenv(app_paths.ENV_DATA_DIR, raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert app_paths.data_dir() is None
    assert app_paths.default_db_path() == "context.db"
    assert app_paths.versions_dir().parts[-2:] == ("models", "versions")
    assert app_paths.versions_dir().parent.parent == Path(app_paths.__file__).resolve().parent
    assert app_paths.install_root() is None


def test_explicit_data_folder_wins(monkeypatch, tmp_path):
    monkeypatch.setenv(app_paths.ENV_DATA_DIR, str(tmp_path))
    assert app_paths.data_dir() == tmp_path
    assert Path(app_paths.default_db_path()) == tmp_path / "context.db"
    assert app_paths.versions_dir() == tmp_path / "models" / "versions"
    assert Path(app_paths.default_run_log_path()) == tmp_path / "adaptation_runs.jsonl"


def test_frozen_app_uses_local_app_data_never_its_own_folder(monkeypatch, tmp_path):
    monkeypatch.delenv(app_paths.ENV_DATA_DIR, raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert app_paths.data_dir() == tmp_path / "context-importance"
    assert app_paths.versions_dir() == tmp_path / "context-importance" / "models" / "versions"


def test_install_root_is_the_folder_above_the_worker_exe(monkeypatch, tmp_path):
    worker = tmp_path / "worker"
    worker.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(worker / "worker.exe"))
    assert app_paths.install_root() == tmp_path
