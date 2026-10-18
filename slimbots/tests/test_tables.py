import sqlite3
import uuid

import pytest

from slimbots import KeyValueTable, LedgerTable, Store


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / f"tables-{uuid.uuid4().hex}.db")


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    yield connection
    connection.close()


def test_key_value_round_trips_and_overwrites(conn):
    kv = KeyValueTable("state")
    kv.create(conn)
    assert kv.get(conn, "a") is None and kv.get(conn, "a", "x") == "x"
    kv.set(conn, "a", 5)
    kv.set(conn, "a", "six")
    assert kv.get(conn, "a") == "six"


def test_advance_moves_integers_forward_only_and_compares_them_as_numbers(conn):
    kv = KeyValueTable()
    kv.create(conn)
    assert kv.advance(conn, "cursor", 9) is True
    assert kv.advance(conn, "cursor", 10) is True
    assert kv.advance(conn, "cursor", 9) is False
    assert kv.get(conn, "cursor") == "10"


def test_advance_compares_strings_as_text(conn):
    kv = KeyValueTable()
    kv.create(conn)
    assert kv.advance(conn, "at", "2024-05-01T00:00:00Z") is True
    assert kv.advance(conn, "at", "2024-04-01T00:00:00Z") is False
    assert kv.advance(conn, "at", "2024-06-01T00:00:00Z") is True
    assert kv.get(conn, "at") == "2024-06-01T00:00:00Z"


def test_a_table_name_that_is_not_an_identifier_is_refused():
    with pytest.raises(ValueError):
        KeyValueTable("state; DROP TABLE x")
    with pytest.raises(ValueError):
        LedgerTable("seen", key="a b")


def test_ledger_add_reports_new_keys_and_keeps_the_first_detail(conn):
    ledger = LedgerTable("announced")
    ledger.create(conn)
    assert ledger.add(conn, "k1", "first", at=10) is True
    assert ledger.add(conn, "k1", "second", at=20) is False
    assert ledger.get(conn, "k1") == ("first", 10)
    assert ledger.has(conn, "k1") and not ledger.has(conn, "k2")


def test_ledger_replace_refreshes_detail_and_time(conn):
    ledger = LedgerTable("announced")
    ledger.create(conn)
    ledger.add(conn, "k1", "first", at=10)
    ledger.add(conn, "k1", "second", replace=True, at=20)
    assert ledger.get(conn, "k1") == ("second", 20)


def test_ledger_unseen_keeps_order_and_add_many_counts_new_keys(conn):
    ledger = LedgerTable("announced")
    ledger.create(conn)
    assert ledger.add_many(conn, ["a", ("b", "note"), "a"]) == 2
    assert ledger.unseen(conn, ["c", "a", "d", "b"]) == ["c", "d"]
    assert ledger.keys(conn) == {"a", "b"}


def test_ledger_prune_forgets_old_entries_only(conn):
    ledger = LedgerTable("announced")
    ledger.create(conn)
    ledger.add(conn, "old", at=5)
    ledger.add(conn, "new", at=50)
    assert ledger.prune(conn, 10) == 1
    assert ledger.keys(conn) == {"new"}


def test_a_ledger_without_a_detail_column_uses_the_named_columns(conn):
    ledger = LedgerTable("processed_requests", key="request_id", detail=None, at="handled_at")
    ledger.create(conn)
    assert ledger.add(conn, "r1", at=7) is True
    assert ledger.add(conn, "r1", at=8) is False
    assert ledger.get(conn, "r1") == ("", 7)
    assert [r[0] for r in conn.execute("SELECT name FROM pragma_table_info('processed_requests')")] == ["request_id", "handled_at"]


async def test_store_kv_and_ledger_work_against_a_database_with_the_old_hand_built_tables(db_path):
    old = sqlite3.connect(db_path)
    old.executescript(
        """
        CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE announced (key TEXT PRIMARY KEY, detail TEXT NOT NULL, at INTEGER NOT NULL);
        CREATE TABLE seen (release_id TEXT PRIMARY KEY, tag TEXT NOT NULL, at INTEGER NOT NULL);
        INSERT INTO state VALUES ('history_cursor', '41');
        INSERT INTO announced VALUES ('grab|1', 'Show S01E01', 100);
        INSERT INTO seen VALUES ('9', 'v1.0', 200);
        """
    )
    old.commit()
    old.close()
    store = await Store(db_path).open()
    try:
        state = await store.kv("state")
        assert await state.get("history_cursor") == "41"
        assert await state.advance("history_cursor", 40) is False
        assert await state.advance("history_cursor", 42) is True
        announced = await store.ledger("announced")
        assert await announced.has("grab|1") and not await announced.add("grab|1")
        seen = await store.ledger("seen", key="release_id", detail="tag")
        assert await seen.has("9") and await seen.add("10", "v1.1")
        assert await seen.unseen(["9", "10", "11"]) == ["11"]
    finally:
        await store.close()
    check = sqlite3.connect(db_path)
    assert check.execute("SELECT value FROM state WHERE key = 'history_cursor'").fetchone() == ("42",)
    assert check.execute("SELECT tag FROM seen WHERE release_id = '10'").fetchone() == ("v1.1",)
    check.close()


async def test_async_calls_commit_so_another_connection_sees_them(db_path):
    store = await Store(db_path, isolation_level="DEFERRED").open()
    try:
        state = await store.kv()
        await state.set("k", "v")
        ledger = await store.ledger("done", detail=None)
        await ledger.add("x")
        other = sqlite3.connect(db_path)
        assert other.execute("SELECT value FROM kv WHERE key = 'k'").fetchone() == ("v",)
        assert other.execute("SELECT key FROM done").fetchone() == ("x",)
        other.close()
        assert await ledger.prune(10**12) == 1
    finally:
        await store.close()
