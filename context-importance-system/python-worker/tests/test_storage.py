"""Tests for the SQLite context store: schema/migrations, insert/query round trips, the
label-construction query, and the storage report.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from storage.context_store import (
    ContextRecord,
    ModelVersion,
    get_active_model_version,
    get_context_record,
    insert_context_record,
    insert_model_version,
    query_unlabelled_older_than,
    set_active_model_version,
    set_label,
)
from storage.database import connect, current_schema_version, decode_embedding, encode_embedding, migrate
from storage.schema import MIGRATIONS, SCHEMA_VERSION

NOW = datetime(2026, 9, 24, 18, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn():
    c = connect(":memory:")
    migrate(c)
    yield c
    c.close()


def make_record(ts=NOW, decision="save", app="chrome", embedding=None, **kw) -> ContextRecord:
    return ContextRecord(
        ts=ts,
        decision=decision,
        features={"beh_key_presses": 5, "vis_novelty": 1.0},
        embedding=embedding,
        app=app,
        domain="docs.example.com",
        title="Docs",
        **kw,
    )


# --------------------------------------------------------------------------- migrations

def test_migrate_is_idempotent(conn) -> None:
    assert current_schema_version(conn) == SCHEMA_VERSION
    assert migrate(conn) == []  # nothing pending on a second run


def test_fresh_database_reports_version_zero() -> None:
    c = connect(":memory:")
    try:
        assert current_schema_version(c) == 0
        applied = migrate(c)
        assert applied == [version for version, _, _ in MIGRATIONS]
        assert applied[-1] == SCHEMA_VERSION
    finally:
        c.close()


def test_a_failing_migration_leaves_no_partial_schema(monkeypatch) -> None:
    import sqlite3

    import storage.database as database

    broken = [(1, "half-applied", "CREATE TABLE first_half (x INTEGER);\nTHIS IS NOT SQL;")]
    monkeypatch.setattr(database, "MIGRATIONS", broken)
    c = connect(":memory:")
    try:
        with pytest.raises(sqlite3.OperationalError):
            migrate(c)
        tables = {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "first_half" not in tables          # the statement before the error was rolled back
        assert current_schema_version(c) == 0      # and the version was not recorded
    finally:
        c.close()


def test_label_version_column_exists(conn) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(context_records)")}
    assert "label_version" in columns


def test_partial_unlabelled_index_exists(conn) -> None:
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "ix_context_unlabelled" in names


# --------------------------------------------------------------------------- context records

def test_insert_and_get_round_trip_with_embedding(conn) -> None:
    embedding = np.arange(512, dtype=np.float32) / 512.0
    record_id = insert_context_record(conn, make_record(embedding=embedding))

    fetched = get_context_record(conn, record_id)
    assert fetched is not None
    assert fetched.decision == "save"
    assert fetched.app == "chrome"
    assert fetched.features["beh_key_presses"] == 5
    assert fetched.label is None
    np.testing.assert_array_equal(fetched.embedding, embedding)
    # Timestamp survives the epoch-microsecond round trip.
    assert fetched.ts == NOW


def test_embedding_is_optional(conn) -> None:
    record_id = insert_context_record(conn, make_record(embedding=None))
    assert get_context_record(conn, record_id).embedding is None


def test_decision_is_constrained(conn) -> None:
    with pytest.raises(Exception):
        insert_context_record(conn, make_record(decision="maybe"))


def test_wrong_embedding_dim_is_rejected(conn) -> None:
    with pytest.raises(ValueError, match="512"):
        insert_context_record(conn, make_record(embedding=np.zeros(256, dtype=np.float32)))


# --------------------------------------------------------------------------- label-construction query

def test_unlabelled_older_than_filters_by_age_and_label(conn) -> None:
    old_unlabelled = insert_context_record(conn, make_record(ts=NOW - timedelta(hours=5)))
    recent_unlabelled = insert_context_record(conn, make_record(ts=NOW - timedelta(minutes=30)))
    old_labelled = insert_context_record(conn, make_record(ts=NOW - timedelta(hours=6)))
    set_label(conn, old_labelled, label=1, labeled_at=NOW - timedelta(hours=1))

    results = query_unlabelled_older_than(conn, hours=2, now=NOW)

    ids = [r.id for r in results]
    assert ids == [old_unlabelled]  # recent one too new; labelled one excluded
    assert recent_unlabelled not in ids


def test_unlabelled_results_are_oldest_first_and_limited(conn) -> None:
    ids = [
        insert_context_record(conn, make_record(ts=NOW - timedelta(hours=h)))
        for h in (3, 5, 4)
    ]
    results = query_unlabelled_older_than(conn, hours=2, now=NOW, limit=2)
    ages = [r.ts for r in results]
    assert ages == sorted(ages)  # ascending by ts
    assert len(results) == 2


def test_set_label_removes_row_from_unlabelled(conn) -> None:
    rid = insert_context_record(conn, make_record(ts=NOW - timedelta(hours=5)))
    assert len(query_unlabelled_older_than(conn, hours=2, now=NOW)) == 1
    set_label(conn, rid, label=0)
    assert query_unlabelled_older_than(conn, hours=2, now=NOW) == []


def test_set_label_records_the_label_version(conn) -> None:
    rid = insert_context_record(conn, make_record(ts=NOW - timedelta(hours=5)))
    assert get_context_record(conn, rid).label_version is None
    set_label(conn, rid, label=1, label_version="v2/h48")
    fetched = get_context_record(conn, rid)
    assert fetched.label == 1
    assert fetched.label_version == "v2/h48"


def test_query_uses_partial_index(conn) -> None:
    insert_context_record(conn, make_record(ts=NOW - timedelta(hours=5)))
    cutoff_us = int((NOW - timedelta(hours=2)).timestamp() * 1_000_000)
    plan = conn.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM context_records WHERE label IS NULL AND ts < ? ORDER BY ts",
        (cutoff_us,),
    ).fetchall()
    detail = " ".join(row["detail"] for row in plan)
    assert "ix_context_unlabelled" in detail


# --------------------------------------------------------------------------- model versions

def test_model_version_activation_is_exclusive(conn) -> None:
    v1 = insert_model_version(conn, ModelVersion(version="0.1.0", algorithm="lightgbm", is_active=True))
    v2 = insert_model_version(conn, ModelVersion(version="0.2.0", algorithm="lightgbm", is_active=True))

    active = get_active_model_version(conn)
    assert active.id == v2
    assert active.version == "0.2.0"

    set_active_model_version(conn, v1)
    assert get_active_model_version(conn).id == v1
    # Still exactly one active row.
    assert conn.execute("SELECT COUNT(*) FROM model_versions WHERE is_active = 1").fetchone()[0] == 1


def test_model_version_metrics_round_trip(conn) -> None:
    vid = insert_model_version(conn, ModelVersion(version="1.0.0", metrics={"auc": 0.91, "n": 1000}))
    active = insert_model_version(conn, ModelVersion(version="1.1.0", is_active=True))
    from storage.context_store import list_model_versions

    versions = {mv.version: mv for mv in list_model_versions(conn)}
    assert versions["1.0.0"].metrics == {"auc": 0.91, "n": 1000}
    assert versions["1.1.0"].id == active


def test_context_links_to_model_version(conn) -> None:
    vid = insert_model_version(conn, ModelVersion(version="0.1.0", is_active=True))
    rid = insert_context_record(conn, make_record(model_version_id=vid))
    assert get_context_record(conn, rid).model_version_id == vid


# --------------------------------------------------------------------------- encoders

def test_embedding_encode_decode_is_lossless() -> None:
    v = np.random.default_rng(0).standard_normal(512).astype(np.float32)
    np.testing.assert_array_equal(decode_embedding(encode_embedding(v)), v)


def test_encode_none_is_none() -> None:
    assert encode_embedding(None) is None
    assert decode_embedding(None) is None


# --------------------------------------------------------------------------- report

def test_storage_report_counts(tmp_path) -> None:
    db_file = tmp_path / "context.db"
    c = connect(str(db_file))
    migrate(c)
    insert_context_record(c, make_record(embedding=np.zeros(512, dtype=np.float32)))
    insert_context_record(c, make_record(decision="discard"))
    labelled = insert_context_record(c, make_record())
    set_label(c, labelled, label=1)
    c.close()

    from storage.report_storage import collect

    stats = collect(str(db_file))
    assert stats["context_records"] == 3
    assert stats["saved"] == 2
    assert stats["discarded"] == 1
    assert stats["unlabelled"] == 2
    assert stats["records_with_embedding"] == 1
    assert stats["embedding_bytes"] == 512 * 4
    assert stats["total_on_disk_bytes"] > 0
