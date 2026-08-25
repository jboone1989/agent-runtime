"""Dataclass JSON codec used by persistence. No third-party serializer."""
from __future__ import annotations

import importlib
import json
from dataclasses import fields, is_dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from types import UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints


def _qual(cls: type) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


def _load(path: str) -> type:
    if not path.startswith("agent_runtime."):
        raise ValueError(f"state type is outside agent_runtime: {path!r}")
    mod_name, _, name = path.rpartition(".")
    mod = importlib.import_module(mod_name)
    return getattr(mod, name)


def encode(obj: Any) -> Any:
    # str/int Enum subclasses must be handled before their primitive bases.
    if isinstance(obj, Enum):
        return {"__enum__": _qual(type(obj)), "value": obj.value}
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, datetime):
        return {"__dt__": obj.isoformat()}
    if isinstance(obj, timedelta):
        return {"__td__": obj.total_seconds()}
    if isinstance(obj, Path):
        return {"__path__": str(obj)}
    if isinstance(obj, set):
        return {"__set__": [encode(x) for x in obj]}
    if is_dataclass(obj) and not isinstance(obj, type):
        data = {f.name: encode(getattr(obj, f.name)) for f in fields(obj)}
        return {"__dc__": _qual(type(obj)), "data": data}
    if isinstance(obj, dict):
        return {str(k): encode(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [encode(x) for x in obj]
    raise TypeError(f"cannot encode {type(obj)!r}")


def decode(obj: Any) -> Any:
    if not isinstance(obj, dict):
        if isinstance(obj, list):
            return [decode(x) for x in obj]
        return obj
    keys = set(obj)
    if keys == {"__dt__"}:
        return datetime.fromisoformat(obj["__dt__"])
    if keys == {"__td__"}:
        return timedelta(seconds=obj["__td__"])
    if keys == {"__path__"}:
        return Path(obj["__path__"])
    if keys == {"__enum__", "value"}:
        cls = _load(obj["__enum__"])
        return cls(obj["value"])
    if keys == {"__set__"}:
        return {decode(x) for x in obj["__set__"]}
    if keys == {"__dc__", "data"}:
        cls = _load(obj["__dc__"])
        kwargs = {k: decode(v) for k, v in obj["data"].items()}
        hints = get_type_hints(cls)
        kwargs = {
            key: _restore_enums(value, hints.get(key))
            for key, value in kwargs.items()
        }
        return cls(**kwargs)
    return {k: decode(v) for k, v in obj.items()}


def dump(obj: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(encode(obj), ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    tmp.replace(path)


def load(path: Path) -> Any:
    return decode(json.loads(path.read_text(encoding="utf-8")))


def _restore_enums(value: Any, annotation: Any) -> Any:
    """Coerce legacy plain enum values using the dataclass field annotation."""
    if annotation is None or value is None:
        return value
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return value if isinstance(value, annotation) else annotation(value)
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (list, set, tuple) and args:
        converted = [_restore_enums(item, args[0]) for item in value]
        return origin(converted)
    if origin is dict and len(args) == 2:
        return {
            _restore_enums(key, args[0]): _restore_enums(item, args[1])
            for key, item in value.items()
        }
    if origin in (Union, UnionType):
        for item_type in args:
            if isinstance(item_type, type) and issubclass(item_type, Enum):
                return _restore_enums(value, item_type)
    return value
