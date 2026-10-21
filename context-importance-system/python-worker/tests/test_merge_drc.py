from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import merge_drc_into_live as m
from storage.context_store import ContextRecord, insert_context_record
from storage.database import connect, migrate, to_epoch_us

T0 = datetime(2026, 10, 19, 13, 0, tzinfo=timezone.utc)


def cap(minutes: int, app="Code", title="x"):
    return SimpleNamespace(ts=T0 + timedelta(minutes=minutes), app=app, title=title)


def live_db(tmp_path, *offsets):
    conn = connect(tmp_path / "live.db")
    migrate(conn)
    for o in offsets:
        insert_context_record(conn, ContextRecord(ts=T0 + timedelta(minutes=o), decision="save", features={}, app="Code", title="live"))
    return conn


def test_cutoff_is_the_oldest_live_record(tmp_path):
    conn = live_db(tmp_path, 5, 1, 9)
    assert m.cutoff_us(conn) == to_epoch_us(T0 + timedelta(minutes=1))


def test_empty_live_database_has_no_cutoff(tmp_path):
    assert m.cutoff_us(live_db(tmp_path)) is None


def test_split_drops_everything_at_or_after_the_live_start(tmp_path):
    cutoff = to_epoch_us(T0)
    kept, dropped = m.split_captures([cap(-30), cap(-1), cap(0), cap(40)], cutoff)
    assert [c.ts for c in kept] == [T0 - timedelta(minutes=30), T0 - timedelta(minutes=1)]
    assert len(dropped) == 2


def test_rerun_only_adds_what_is_missing(tmp_path):
    conn = live_db(tmp_path, 10)
    insert_context_record(conn, ContextRecord(ts=T0 - timedelta(minutes=5), decision="save", features={}, app="Code", title="x"))
    fresh = m.drop_already_present([cap(-5), cap(-6)], m.existing_keys(conn))
    assert [c.ts for c in fresh] == [T0 - timedelta(minutes=6)]


def test_registered_cutoff_survives_and_is_reused(tmp_path):
    conn = live_db(tmp_path, 10)
    first = m.cutoff_us(conn)
    # a merge adds older rows; the cutoff must stay the original live start, not move to the new oldest row
    insert_context_record(conn, ContextRecord(ts=T0 - timedelta(days=3), decision="save", features={}, app="Code", title="old"))
    m.register_source(conn, first, 1)
    assert m.cutoff_us(conn) == first
    m.register_source(conn, first, 2)
    assert conn.execute("SELECT record_count FROM record_sources").fetchone()[0] == 3


def test_backup_copies_every_record(tmp_path):
    live_db(tmp_path, 1, 2, 3).close()
    target = m.backup(tmp_path / "live.db", tmp_path / "bk")
    copy = connect(target, read_only=True)
    assert copy.execute("SELECT COUNT(*) FROM context_records").fetchone()[0] == 3
