from __future__ import annotations

import sqlite3

import pytest

from agent_runtime.observation import DATABASE_READ_OBSERVE, RuntimeObservationProvider
from agent_runtime.observation.database_read import (
    DatabaseReadPolicy,
    DatabaseSource,
    SQLAlchemyDatabaseRead,
)


def _database(tmp_path):
    path = tmp_path / "sample.db"
    connection = sqlite3.connect(path)
    connection.execute("create table items (id integer primary key, name text, score integer)")
    connection.execute("create table hidden (id integer primary key, secret text)")
    connection.executemany(
        "insert into items (name, score) values (?, ?)",
        [("alpha", 10), ("beta", 30), ("gamma", 20)],
    )
    connection.commit()
    connection.close()
    return path


def _extension(tmp_path, **policy_kwargs):
    path = _database(tmp_path)
    source = DatabaseSource(
        url=f"sqlite:///{path}",
        allowed_tables=frozenset({"items"}),
    )
    return SQLAlchemyDatabaseRead(
        sources={"local": source},
        policy=DatabaseReadPolicy(**policy_kwargs),
    )


def test_database_select_is_structured_allowlisted_and_bounded(tmp_path) -> None:
    extension = _extension(tmp_path, max_rows=2)
    provider = RuntimeObservationProvider(extensions=(extension,))

    result = provider.invoke(
        DATABASE_READ_OBSERVE,
        {
            "table": "items",
            "columns": ["id", "name", "score"],
            "where": [{"column": "score", "op": "gte", "value": 10}],
            "order_by": "score",
            "descending": True,
            "limit": 2,
        },
    )

    assert result["ok"] is True
    assert [row["name"] for row in result["data"]["rows"]] == ["beta", "gamma"]
    assert result["data"]["truncated"] is True
    assert result["evidence"]["raw_sql_accepted"] is False
    assert result["evidence"]["read_only_enforced"] is True
    assert "sqlite:///" not in repr(result)
    provider.close()


def test_database_introspection_is_filtered_by_table_allowlist(tmp_path) -> None:
    extension = _extension(tmp_path)
    provider = RuntimeObservationProvider(extensions=(extension,))

    tables = provider.invoke(DATABASE_READ_OBSERVE, {"operation": "tables"})
    columns = provider.invoke(
        DATABASE_READ_OBSERVE,
        {"operation": "columns", "table": "items"},
    )

    assert tables["data"]["tables"] == ["items"]
    assert [row["name"] for row in columns["data"]["columns"]] == ["id", "name", "score"]
    provider.close()


def test_database_rejects_raw_sql_unknown_tables_and_columns(tmp_path) -> None:
    extension = _extension(tmp_path)
    provider = RuntimeObservationProvider(extensions=(extension,))

    raw = provider.invoke(DATABASE_READ_OBSERVE, {"table": "items", "raw_sql": "select 1"})
    hidden = provider.invoke(DATABASE_READ_OBSERVE, {"table": "hidden"})
    missing = provider.invoke(DATABASE_READ_OBSERVE, {"table": "items", "columns": ["missing"]})

    assert raw["error"]["code"] == "unknown_parameter"
    assert hidden["error"]["code"] == "table_not_allowed"
    assert missing["error"]["code"] == "column_not_allowed"
    provider.close()


def test_database_source_must_be_declared_read_only() -> None:
    with pytest.raises(ValueError, match="read-only"):
        DatabaseSource(
            url="sqlite://",
            allowed_tables=frozenset({"items"}),
            read_only=False,
        )


def test_database_timeout_fails_closed(tmp_path) -> None:
    extension = _extension(tmp_path, timeout_seconds=1e-9)
    provider = RuntimeObservationProvider(extensions=(extension,))

    result = provider.invoke(DATABASE_READ_OBSERVE, {"table": "items"})

    assert result["ok"] is False
    assert result["error"]["code"] in {"database_timeout", "database_read_failed"}
    provider.close()


def test_database_extension_restarts_without_persisting_connections(tmp_path) -> None:
    path = _database(tmp_path)
    source = DatabaseSource(url=f"sqlite:///{path}", allowed_tables=frozenset({"items"}))
    first = SQLAlchemyDatabaseRead(sources={"local": source})
    provider = RuntimeObservationProvider(extensions=(first,))
    assert provider.invoke(DATABASE_READ_OBSERVE, {"table": "items", "limit": 1})["ok"] is True
    provider.close()
    assert first._engines == {}

    second = SQLAlchemyDatabaseRead(sources={"local": source})
    restarted = RuntimeObservationProvider(extensions=(second,))
    assert restarted.invoke(DATABASE_READ_OBSERVE, {"table": "items", "limit": 1})["ok"] is True
    restarted.close()
