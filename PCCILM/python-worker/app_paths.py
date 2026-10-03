"""Where the worker keeps user data: one place, so an installed (frozen) app never writes next to its code.

    CONTEXT_IMPORTANCE_DATA  explicit data folder (wins everywhere)
    frozen (PyInstaller)     %LOCALAPPDATA%/context-importance
    running from source      the legacy repo layout, so existing databases and model versions keep working

Under a data folder: ``context.db``, ``raw_buffer.db`` (screen text), ``models/versions/`` (append-only), ``adaptation_runs.jsonl``, logs.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ENV_DATA_DIR = "CONTEXT_IMPORTANCE_DATA"
_SOURCE_DIR = Path(__file__).resolve().parent


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def data_dir() -> Path | None:
    """The user-data folder, or ``None`` when running from source without an explicit one."""
    explicit = os.environ.get(ENV_DATA_DIR)
    if explicit:
        return Path(explicit)
    if is_frozen():
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "context-importance"
    return None


def versions_dir() -> Path:
    """Model and prototype versions (append-only)."""
    base = data_dir()
    return base / "models" / "versions" if base else _SOURCE_DIR / "models" / "versions"


def default_db_path() -> str:
    base = data_dir()
    return str(base / "context.db") if base else "context.db"


def default_run_log_path() -> str:
    base = data_dir()
    return str(base / "adaptation_runs.jsonl") if base else "adaptation_runs.jsonl"


def install_root() -> Path | None:
    """The folder holding ``worker/`` and ``models/`` in an installed app, else ``None``."""
    if not is_frozen():
        return None
    return Path(sys.executable).resolve().parent.parent
