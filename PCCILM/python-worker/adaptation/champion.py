"""Choose the champion: the model whose decisions the worker enforces. The dashboard's Make champion button runs this
(``adapt.exe champion set <version>``). Nothing is deleted: the previous champion stays a saved version, keeps scoring
in shadow mode after the next restart, and can be made champion again the same way. Every change is recorded in
``champion_events``.

    python -m adaptation.champion --db context.db list
    python -m adaptation.champion --db context.db set logreg-20261025-031500 --reason "best live AP over 7 days"

The worker loads the champion at startup only, so restart capture afterwards.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from models import registry
from models.lightgbm_model import DEFAULT_VERSIONS_DIR


def make_champion(conn, version: str, *, versions_dir: Path | str = DEFAULT_VERSIONS_DIR, reason: str = "manual") -> dict:
    """Make ``version`` the champion after checking its saved model loads. Raises ValueError when it does not exist
    or cannot be loaded (the current champion then stays)."""
    from storage.context_store import get_active_model_version, get_model_version, set_champion

    target = get_model_version(conn, version)
    if target is None:
        raise ValueError(f"no model version {version!r}")
    try:
        registry.load_scorer(target.algorithm, version, versions_dir)
    except Exception as exc:
        raise ValueError(f"{version!r} cannot be loaded ({type(exc).__name__}: {exc}); the champion is unchanged") from exc
    previous = get_active_model_version(conn)
    if previous is not None and previous.version == version:
        return {"champion": version, "changed": False, "previous": version}
    set_champion(conn, version, reason=reason)
    return {"champion": version, "changed": True, "previous": previous.version if previous else None,
            "algorithm": registry.algorithm_of(target.algorithm)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True)
    parser.add_argument("--versions-dir", default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="every saved version, newest first, with the champion marked")
    chosen = sub.add_parser("set", help="make a version the champion")
    chosen.add_argument("version")
    chosen.add_argument("--reason", default="manual (dashboard)")
    args = parser.parse_args(argv)

    from storage.context_store import list_model_versions
    from storage.database import connect, migrate

    conn = connect(args.db)
    migrate(conn)
    try:
        if args.command == "list":
            for v in reversed(list_model_versions(conn)):
                print(f"{'*' if v.is_active else ' '} {registry.algorithm_of(v.algorithm):28} {v.version}")
            return 0
        try:
            result = make_champion(conn, args.version, versions_dir=args.versions_dir or DEFAULT_VERSIONS_DIR, reason=args.reason)
        except ValueError as exc:
            print(json.dumps({"error": str(exc)}))
            return 1
        print(json.dumps(result))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
