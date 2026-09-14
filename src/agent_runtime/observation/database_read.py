"""Bounded read-only database observation through SQLAlchemy Core."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time
from decimal import Decimal
import json
import re
import time
from typing import Any
from uuid import UUID

from .contract import ObservationFailure
from .provider import DATABASE_READ_OBSERVE

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_FILTER_OPS = frozenset({"eq", "ne", "lt", "lte", "gt", "gte", "in", "is_null"})


@dataclass(frozen=True)
class DatabaseSource:
    url: str
    allowed_tables: frozenset[str]
    schema: str | None = None
    read_only: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.url, str) or not self.url.strip():
            raise ValueError("database url must be non-empty")
        if not self.read_only:
            raise ValueError("database observation requires a read-only source")
        tables = frozenset(str(item) for item in self.allowed_tables)
        if not tables or any(not _IDENTIFIER.fullmatch(item) for item in tables):
            raise ValueError("allowed_tables must contain simple SQL identifiers")
        if self.schema is not None and not _IDENTIFIER.fullmatch(self.schema):
            raise ValueError("database schema must be a simple SQL identifier")
        object.__setattr__(self, "allowed_tables", tables)


@dataclass(frozen=True)
class DatabaseReadPolicy:
    max_rows: int = 200
    max_columns: int = 64
    max_filters: int = 16
    max_bytes: int = 1_048_576
    timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if not 1 <= int(self.max_rows) <= 10_000:
            raise ValueError("max_rows must be in [1, 10000]")
        if not 1 <= int(self.max_columns) <= 256:
            raise ValueError("max_columns must be in [1, 256]")
        if not 0 <= int(self.max_filters) <= 64:
            raise ValueError("max_filters must be in [0, 64]")
        if not 1 <= int(self.max_bytes) <= 16 * 1024 * 1024:
            raise ValueError("max_bytes must be in [1, 16777216]")
        if not 0 < float(self.timeout_seconds) <= 60:
            raise ValueError("timeout_seconds must be in (0, 60]")


class SQLAlchemyDatabaseRead:
    """ObservationExtension that exposes only structured SELECT/introspection."""

    capability_id = DATABASE_READ_OBSERVE

    def __init__(
        self,
        *,
        sources: Mapping[str, DatabaseSource],
        policy: DatabaseReadPolicy | None = None,
    ) -> None:
        if not sources or any(not isinstance(key, str) or not key for key in sources):
            raise ValueError("database sources require non-empty source identifiers")
        self.sources = dict(sources)
        self.policy = policy or DatabaseReadPolicy()
        self._engines: dict[str, Any] = {}

    def observe(self, payload: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        if not isinstance(payload, Mapping):
            raise ObservationFailure("invalid_request", "database request must be an object")
        request = dict(payload)
        source_id, source = self._select_source(request)
        operation = request.get("operation", "select")
        if operation not in {"select", "tables", "columns"}:
            raise ObservationFailure("unsupported_operation", "database operation must be select, tables, or columns")
        engine = self._engine(source_id, source)
        with engine.connect() as connection:
            dialect = connection.dialect.name
            self._apply_read_only_guard(connection, dialect)
            started = time.monotonic()
            deadline = started + self.policy.timeout_seconds
            cleanup = self._install_timeout(connection, dialect, deadline)
            try:
                if operation == "tables":
                    self._reject_unknown(request, {"operation", "source_id"})
                    data = self._tables(connection, source)
                elif operation == "columns":
                    self._reject_unknown(request, {"operation", "source_id", "table"})
                    data = self._columns(connection, source, request)
                else:
                    self._reject_unknown(
                        request,
                        {"operation", "source_id", "table", "columns", "where", "limit", "order_by", "descending"},
                    )
                    data = self._select(connection, source, request)
            except ObservationFailure:
                raise
            except Exception as exc:
                if time.monotonic() >= deadline:
                    raise ObservationFailure("database_timeout", "database observation exceeded its time limit") from exc
                raise ObservationFailure("database_read_failed", type(exc).__name__) from exc
            finally:
                cleanup()
        evidence = {
            "library": "sqlalchemy-core",
            "source_id": source_id,
            "dialect": dialect,
            "read_only_enforced": True,
            "raw_sql_accepted": False,
            "max_rows": self.policy.max_rows,
            "max_bytes": self.policy.max_bytes,
            "timeout_seconds": self.policy.timeout_seconds,
        }
        return data, evidence

    def close(self) -> None:
        for engine in self._engines.values():
            engine.dispose()
        self._engines.clear()

    def _select_source(self, request: Mapping[str, Any]) -> tuple[str, DatabaseSource]:
        source_id = request.get("source_id")
        if source_id is None and len(self.sources) == 1:
            source_id = next(iter(self.sources))
        if not isinstance(source_id, str) or source_id not in self.sources:
            raise ObservationFailure("source_not_bound", "a configured database source_id is required")
        return source_id, self.sources[source_id]

    def _engine(self, source_id: str, source: DatabaseSource) -> Any:
        cached = self._engines.get(source_id)
        if cached is not None:
            return cached
        try:
            from sqlalchemy import create_engine
        except ImportError as exc:
            raise ObservationFailure("provider_unavailable", "install agent-runtime[database]") from exc
        engine = create_engine(source.url, pool_pre_ping=True)
        if engine.dialect.name not in {"sqlite", "postgresql"}:
            engine.dispose()
            raise ObservationFailure("dialect_not_supported", "database dialect has no Runtime read-only guard")
        self._engines[source_id] = engine
        return engine

    def _apply_read_only_guard(self, connection: Any, dialect: str) -> None:
        if dialect == "sqlite":
            connection.exec_driver_sql("PRAGMA query_only = ON")
            state = connection.exec_driver_sql("PRAGMA query_only").scalar_one()
            if int(state) != 1:
                raise ObservationFailure("read_only_unavailable", "SQLite query-only mode could not be enabled")
            return
        if dialect == "postgresql":
            transaction = connection.get_transaction()
            if transaction is None:
                connection.begin()
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            timeout_ms = max(1, int(self.policy.timeout_seconds * 1000))
            connection.exec_driver_sql(f"SET LOCAL statement_timeout = {timeout_ms}")
            return
        raise ObservationFailure("dialect_not_supported", "database dialect has no Runtime read-only guard")

    def _install_timeout(self, connection: Any, dialect: str, deadline: float):
        if dialect != "sqlite":
            return lambda: None
        raw = connection.connection.driver_connection
        setter = getattr(raw, "set_progress_handler", None)
        if not callable(setter):
            raise ObservationFailure("timeout_unavailable", "SQLite progress handler is unavailable")
        setter(lambda: 1 if time.monotonic() >= deadline else 0, 1000)
        return lambda: setter(None, 0)

    def _tables(self, connection: Any, source: DatabaseSource) -> dict[str, Any]:
        from sqlalchemy import inspect

        available = set(inspect(connection).get_table_names(schema=source.schema))
        return {"tables": sorted(available & set(source.allowed_tables))}

    def _columns(self, connection: Any, source: DatabaseSource, request: Mapping[str, Any]) -> dict[str, Any]:
        from sqlalchemy import inspect

        table = self._table_name(request, source)
        rows = inspect(connection).get_columns(table, schema=source.schema)
        if len(rows) > self.policy.max_columns:
            raise ObservationFailure("column_limit_exceeded", "table exceeds configured column limit")
        return {
            "table": table,
            "columns": [
                {"name": str(row["name"]), "type": str(row["type"]), "nullable": bool(row.get("nullable", True))}
                for row in rows
            ],
        }

    def _select(self, connection: Any, source: DatabaseSource, request: Mapping[str, Any]) -> dict[str, Any]:
        from sqlalchemy import MetaData, Table, select

        table_name = self._table_name(request, source)
        table = Table(table_name, MetaData(), schema=source.schema, autoload_with=connection)
        if len(table.columns) > self.policy.max_columns:
            raise ObservationFailure("column_limit_exceeded", "table exceeds configured column limit")
        requested_columns = request.get("columns")
        if requested_columns is None:
            columns = list(table.columns)
        else:
            if not isinstance(requested_columns, list) or not requested_columns:
                raise ObservationFailure("invalid_columns", "columns must be a non-empty list")
            if len(requested_columns) > self.policy.max_columns:
                raise ObservationFailure("column_limit_exceeded", "requested columns exceed configured limit")
            columns = [self._column(table, item) for item in requested_columns]
        statement = select(*columns).select_from(table)
        filters = request.get("where", [])
        if not isinstance(filters, list) or len(filters) > self.policy.max_filters:
            raise ObservationFailure("invalid_filters", "where must be a bounded list")
        for item in filters:
            statement = statement.where(self._predicate(table, item))
        order_by = request.get("order_by")
        if order_by is not None:
            column = self._column(table, order_by)
            descending = request.get("descending", False)
            if not isinstance(descending, bool):
                raise ObservationFailure("invalid_order", "descending must be boolean")
            statement = statement.order_by(column.desc() if descending else column.asc())
        elif "descending" in request:
            raise ObservationFailure("invalid_order", "descending requires order_by")
        limit = request.get("limit", self.policy.max_rows)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= self.policy.max_rows:
            raise ObservationFailure("invalid_limit", "limit exceeds configured row bound")
        rows = connection.execute(statement.limit(limit + 1)).mappings().all()
        truncated = len(rows) > limit
        rendered: list[dict[str, Any]] = []
        used_bytes = 0
        for row in rows[:limit]:
            normalized = {str(key): _json_value(value) for key, value in row.items()}
            encoded = json.dumps(normalized, ensure_ascii=False, allow_nan=False, sort_keys=True).encode("utf-8")
            if used_bytes + len(encoded) > self.policy.max_bytes:
                truncated = True
                break
            used_bytes += len(encoded)
            rendered.append(normalized)
        return {
            "table": table_name,
            "columns": [column.name for column in columns],
            "rows": rendered,
            "row_count": len(rendered),
            "truncated": truncated,
            "bytes": used_bytes,
        }

    def _table_name(self, request: Mapping[str, Any], source: DatabaseSource) -> str:
        table = request.get("table")
        if not isinstance(table, str) or table not in source.allowed_tables:
            raise ObservationFailure("table_not_allowed", "table is outside the configured database allowlist")
        return table

    def _column(self, table: Any, value: Any) -> Any:
        if not isinstance(value, str) or value not in table.c:
            raise ObservationFailure("column_not_allowed", "column is not present in the bound table")
        return table.c[value]

    def _predicate(self, table: Any, item: Any) -> Any:
        if not isinstance(item, Mapping) or set(item) - {"column", "op", "value"}:
            raise ObservationFailure("invalid_filter", "filter must contain only column, op, and value")
        column = self._column(table, item.get("column"))
        op = item.get("op", "eq")
        if op not in _FILTER_OPS:
            raise ObservationFailure("invalid_filter", "filter operator is not allowed")
        value = item.get("value")
        if op == "eq":
            return column == value
        if op == "ne":
            return column != value
        if op == "lt":
            return column < value
        if op == "lte":
            return column <= value
        if op == "gt":
            return column > value
        if op == "gte":
            return column >= value
        if op == "is_null":
            if value not in {None, True, False}:
                raise ObservationFailure("invalid_filter", "is_null value must be boolean or null")
            return column.is_(None) if value is not False else column.is_not(None)
        if not isinstance(value, list) or len(value) > 100:
            raise ObservationFailure("invalid_filter", "in filter requires at most 100 values")
        return column.in_(value)

    @staticmethod
    def _reject_unknown(request: Mapping[str, Any], allowed: set[str]) -> None:
        unknown = sorted(set(request) - allowed)
        if unknown:
            raise ObservationFailure("unknown_parameter", "database request contains unsupported parameters", evidence={"parameters": unknown})


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            return str(value)
        return value
    if isinstance(value, (datetime, date, datetime_time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, bytes):
        return {"encoding": "hex", "value": value[:4096].hex(), "truncated": len(value) > 4096}
    text = str(value)
    return text[:4096]


__all__ = ["DatabaseReadPolicy", "DatabaseSource", "SQLAlchemyDatabaseRead"]
