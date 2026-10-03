"""Load one participant's data from the cloud: DRC events from Neon PostgreSQL, screenshots from
the Cloud Storage bucket. Used by ``Colab/03_experiments_colab.ipynb``.

DRC syncs every participant's SQLite tables to Neon (schema ``drc``, same table and column names)
and uploads screenshots to ``gs://<bucket>/<profile_id>/<yyyy>/<mm>/<dd>/<screenshot_id>.jpg``.
:func:`write_drc_sqlite` turns the Neon rows back into DRC's SQLite layout, so
:func:`offline.drc_replay.replay_drc` runs unchanged (checked identical to a direct replay on
5,049 captures).

Read-only: queries filter on one ``profile_id`` and fetch only the columns the replay uses (no
URLs, page titles, hashes or metadata). Nothing is written to Neon or the bucket.
"""

from __future__ import annotations

import re
import sqlite3
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote, unquote

import pandas as pd

# Exactly what offline.drc_replay reads, per table.
REPLAY_COLUMNS: dict[str, list[str]] = {
    "activity_events": ["event_id", "session_id", "timestamp", "event_type", "application_name",
                        "process_name", "window_title", "duration_ms"],
    "browser_events": ["browser_event_id", "session_id", "timestamp", "domain"],
    "interaction_events": ["interaction_id", "session_id", "timestamp", "interaction_type", "count"],
    "screenshots": ["screenshot_id", "session_id", "timestamp", "capture_reason", "file_reference", "file_size"],
}


def clean_database_url(raw: str) -> str:
    """Accept the connection string however Neon's dashboard gave it (plain URL, ``psql '...'``,
    quoted, trailing newline) and return a SQLAlchemy psycopg2 URL with the password percent-encoded."""
    found = re.search(r"postgres(?:ql)?(?:\+\w+)?://[^\s'\"]+", (raw or "").strip())
    if not found:
        raise ValueError("no postgresql://... URL found. In Neon: Connect -> 'Connection string', copy it.")
    rest = re.sub(r"^postgres(?:ql)?(?:\+\w+)?://", "", found.group(0))
    if "@" in rest:  # user:password@host/db?options; the host never contains '@', so split on the last
        credentials, host_part = rest.rsplit("@", 1)
        user, _, password = credentials.partition(":")
        rest = f"{user}:{quote(unquote(password), safe='')}@{host_part}"
    return "postgresql+psycopg2://" + rest


def fetch_profile_tables(
    engine,
    profile_id: str,
    *,
    schema: str = "drc",
    from_date: str | None = None,
    to_date: str | None = None,
    chunksize: int = 200_000,
) -> dict[str, pd.DataFrame]:
    """One participant's rows from each replay table (``REPLAY_COLUMNS`` only), timestamps as UTC."""
    from sqlalchemy import text

    tables = {}
    for table, columns in REPLAY_COLUMNS.items():
        cols = ", ".join(f'"{c}"' for c in columns)
        where, params = ["profile_id = :pid"], {"pid": profile_id}
        if from_date:
            where.append('"timestamp" >= :from_date')
            params["from_date"] = from_date
        if to_date:
            where.append('"timestamp" < (CAST(:to_date AS date) + 1)')
            params["to_date"] = to_date
        sql = text(f'SELECT {cols} FROM "{schema}"."{table}" WHERE {" AND ".join(where)} ORDER BY "timestamp"')
        with engine.connect() as conn:
            chunks = list(pd.read_sql(sql, conn, params=params, chunksize=chunksize))
        df = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame(columns=columns)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        tables[table] = df
    return tables


def write_drc_sqlite(tables: dict[str, pd.DataFrame], path: Path | str) -> Path:
    """Write Neon rows as a DRC-shaped SQLite file (replaced if it exists).

    Timestamps become fixed-width UTC ISO text, so ``ORDER BY timestamp`` on the text is time order.
    Screenshots get ``local_file_path`` = the file name from ``file_reference``; rows without one
    are dropped.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    shots = tables["screenshots"].dropna(subset=["file_reference"]).copy()
    shots["local_file_path"] = shots["file_reference"].str.rsplit("/", n=1).str[-1]
    out_tables = {**tables, "screenshots": shots.drop(columns=["file_reference"])}
    with sqlite3.connect(path) as conn:
        for name in REPLAY_COLUMNS:
            df = out_tables[name].copy()
            df["timestamp"] = (pd.to_datetime(df["timestamp"], utc=True)
                               .dt.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00"))
            df.to_sql(name, conn, index=False)
    return path


def download_profile_screenshots(
    client,
    bucket: str,
    profile_id: str,
    dest: Path | str,
    *,
    workers: int = 16,
    progress: bool = True,
) -> dict[str, int]:
    """Copy every ``.jpg`` under ``gs://<bucket>/<profile_id>/`` into one flat folder
    (``<screenshot_id>.jpg``), skipping files already present with the same size. Same method as the
    author's earlier import notebook, flattened because the replay finds images by file name."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    blobs = [b for b in client.list_blobs(bucket, prefix=f"{profile_id}/") if b.name.endswith(".jpg")]

    def download(blob) -> str:
        target = dest / blob.name.rsplit("/", 1)[-1]
        if target.exists() and target.stat().st_size == blob.size:
            return "skipped"
        blob.download_to_filename(str(target))
        return "downloaded"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs = pool.map(download, blobs)
        if progress:
            from tqdm.auto import tqdm

            jobs = tqdm(jobs, total=len(blobs), desc="Downloading", unit="file")
        counts = Counter(jobs)
    return {"listed": len(blobs), "skipped": counts["skipped"], "downloaded": counts["downloaded"]}


__all__ = ["REPLAY_COLUMNS", "clean_database_url", "download_profile_screenshots", "fetch_profile_tables", "write_drc_sqlite"]
