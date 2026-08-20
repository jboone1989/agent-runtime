"""Dataclass JSON codec used by persistence. No third-party serializer."""
from __future__ import annotations

import importlib
import json
from dataclasses import fields, is_dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any


def _qual(cls: type) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


def _load(path: str) -> type:
    mod_name, _, name = path.rpartition(".")
    mod = importlib.import_module(mod_name)
    return getattr(mod, name)


def encode(obj: Any) -> Any:
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, datetime):
        return {"__dt__": obj.isoformat()}
    if isinstance(obj, timedelta):
        return {"__td__": obj.total_seconds()}
    if isinstance(obj, Path):
        return {"__path__": str(obj)}
    if isinstance(obj, Enum):
        return {"__enum__": _qual(type(obj)), "value": obj.value}
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
    if "__dt__" in obj:
        return datetime.fromisoformat(obj["__dt__"])
    if "__td__" in obj:
        return timedelta(seconds=obj["__td__"])
    if "__path__" in obj:
        return Path(obj["__path__"])
    if "__enum__" in obj:
        cls = _load(obj["__enum__"])
        return cls(obj["value"])
    if "__set__" in obj:
        return {decode(x) for x in obj["__set__"]}
    if "__dc__" in obj:
        cls = _load(obj["__dc__"])
        kwargs = {k: decode(v) for k, v in obj["data"].items()}
        return cls(**kwargs)
    return {k: decode(v) for k, v in obj.items()}


def dump(obj: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(encode(obj), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load(path: Path) -> Any:
    return decode(json.loads(path.read_text(encoding="utf-8")))
