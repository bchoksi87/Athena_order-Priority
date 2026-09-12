"""(De)serialisation of :class:`PlanningSnapshot` and other domain dataclasses.

Encoding is generic: dataclasses become dicts, enums their ``.value``,
``datetime``/``date``/``time`` ISO-8601 strings, sets sorted lists (for
deterministic output), tuples lists. Decoding is driven by the dataclass type
hints so every value comes back with its exact Python type (sets stay sets,
tuples stay tuples, enums are re-instantiated, datetimes are aware UTC).

The snapshot payload stored in ``input_snapshots`` is ``gzip(json)``; the
codec name is recorded next to it so the format can evolve.
"""

from __future__ import annotations

import dataclasses
import gzip
import hashlib
import json
import types
import typing
from collections.abc import Mapping
from datetime import UTC, date, datetime, time
from enum import Enum
from typing import Any, TypeVar, get_args, get_origin, get_type_hints

from app.core.errors import ValidationError
from app.domain.models import Shift, TimeWindow
from app.domain.snapshot import PlanningSnapshot

CODEC_GZIP_JSON = "gzip+json/v1"
T = TypeVar("T")

# ----------------------------------------------------------------- encoding


def to_jsonable(value: Any) -> Any:
    """Recursively convert domain values into JSON-compatible structures."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValidationError("naive datetime cannot be encoded; all datetimes must be aware UTC")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            f.name: to_jsonable(getattr(value, f.name))
            for f in dataclasses.fields(value)
            if not f.name.startswith("_")
        }
    if isinstance(value, set | frozenset):
        return sorted((to_jsonable(v) for v in value), key=_sort_key)
    if isinstance(value, list | tuple):
        return [to_jsonable(v) for v in value]
    if isinstance(value, Mapping):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    raise ValidationError(f"cannot encode value of type {type(value).__name__}")


def _sort_key(value: Any) -> tuple[str, str]:
    return (type(value).__name__, json.dumps(value, sort_keys=True, default=str))


def dumps(value: Any) -> str:
    """Deterministic JSON text (sorted keys, compact separators)."""
    return json.dumps(to_jsonable(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# ----------------------------------------------------------------- decoding


def _is_optional(hint: Any) -> bool:
    origin = get_origin(hint)
    return origin in (typing.Union, types.UnionType) and type(None) in get_args(hint)


def _strip_optional(hint: Any) -> Any:
    args = [a for a in get_args(hint) if a is not type(None)]
    if len(args) == 1:
        return args[0]
    return typing.Union[tuple(args)]  # noqa: UP007 - runtime construction


def decode_value(value: Any, hint: Any) -> Any:
    """Convert a JSON value into the Python type described by ``hint``."""
    if hint is Any or hint is object:
        return value
    if _is_optional(hint):
        if value is None:
            return None
        return decode_value(value, _strip_optional(hint))
    origin = get_origin(hint)
    if origin in (typing.Union, types.UnionType):
        for candidate in get_args(hint):
            try:
                return decode_value(value, candidate)
            except (TypeError, ValueError):
                continue
        raise ValidationError(f"value {value!r} matches none of {hint}")
    if isinstance(hint, type):
        if issubclass(hint, Enum):
            return hint(value)
        if hint is datetime:
            return _parse_datetime(value)
        if hint is date:
            return date.fromisoformat(value)
        if hint is time:
            return time.fromisoformat(value)
        if hint is bool:
            return bool(value)
        if hint is int:
            return int(value)
        if hint is float:
            return float(value)
        if hint is str:
            return str(value)
        if dataclasses.is_dataclass(hint):
            return decode_dataclass(value, hint)
        return value
    if origin in (list, typing.Sequence):
        (item_hint,) = get_args(hint) or (Any,)
        return [decode_value(v, item_hint) for v in value]
    if origin in (set, frozenset):
        (item_hint,) = get_args(hint) or (Any,)
        result = {decode_value(v, item_hint) for v in value}
        return frozenset(result) if origin is frozenset else result
    if origin is tuple:
        args = get_args(hint)
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(decode_value(v, args[0]) for v in value)
        if args:
            return tuple(decode_value(v, h) for v, h in zip(value, args, strict=True))
        return tuple(value)
    if origin in (dict, typing.Mapping):
        key_hint, val_hint = get_args(hint) or (str, Any)
        return {decode_value(k, key_hint): decode_value(v, val_hint) for k, v in value.items()}
    return value


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def decode_dataclass(data: Mapping[str, Any], cls: type[T]) -> T:
    """Instantiate dataclass ``cls`` from a JSON dict, converting every field by type hint."""
    if not isinstance(data, Mapping):
        raise ValidationError(f"expected object for {cls.__name__}, got {type(data).__name__}")
    hints = _type_hints(cls)
    kwargs: dict[str, Any] = {}
    for f in dataclasses.fields(cls):  # type: ignore[arg-type]
        if f.name.startswith("_") or not f.init:
            continue
        if f.name not in data:
            continue
        kwargs[f.name] = decode_value(data[f.name], hints.get(f.name, Any))
    return cls(**kwargs)


def _type_hints(cls: type[Any]) -> dict[str, Any]:
    import app.domain.enums as enums_mod
    import app.domain.models as models_mod
    import app.domain.results as results_mod

    namespace: dict[str, Any] = {}
    for mod in (enums_mod, models_mod, results_mod):
        namespace.update(vars(mod))
    namespace.update({"datetime": datetime, "date": date, "time": time, "Any": Any})
    return get_type_hints(cls, globalns=namespace)


# ------------------------------------------------------- small value helpers


def encode_time_window(window: TimeWindow) -> dict[str, Any]:
    return {"start": to_jsonable(window.start), "end": to_jsonable(window.end), "reason": window.reason}


def decode_time_window(data: Mapping[str, Any]) -> TimeWindow:
    return decode_dataclass(data, TimeWindow)


def encode_shift(shift: Shift) -> dict[str, Any]:
    return to_jsonable(shift)


def decode_shift(data: Mapping[str, Any]) -> Shift:
    return decode_dataclass(data, Shift)


def encode_date(value: date) -> str:
    return value.isoformat()


def decode_date(value: str) -> date:
    return date.fromisoformat(value)


# --------------------------------------------------------------- snapshots


def snapshot_to_dict(snapshot: PlanningSnapshot) -> dict[str, Any]:
    return to_jsonable(snapshot)


def snapshot_from_dict(data: Mapping[str, Any]) -> PlanningSnapshot:
    snapshot = decode_dataclass(data, PlanningSnapshot)
    snapshot.rebuild_indexes()
    return snapshot


def encode_snapshot(snapshot: PlanningSnapshot) -> bytes:
    """Serialise to deterministic gzip-compressed JSON bytes."""
    text = dumps(snapshot)
    return gzip.compress(text.encode("utf-8"), compresslevel=6, mtime=0)


def decode_snapshot(payload: bytes, codec: str = CODEC_GZIP_JSON) -> PlanningSnapshot:
    if codec != CODEC_GZIP_JSON:
        raise ValidationError(f"unsupported snapshot codec '{codec}'", details={"codec": codec})
    try:
        data = json.loads(gzip.decompress(payload).decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise ValidationError("snapshot payload is corrupt") from exc
    return snapshot_from_dict(data)


def payload_digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "CODEC_GZIP_JSON",
    "decode_dataclass",
    "decode_date",
    "decode_shift",
    "decode_snapshot",
    "decode_time_window",
    "decode_value",
    "dumps",
    "encode_date",
    "encode_shift",
    "encode_snapshot",
    "encode_time_window",
    "payload_digest",
    "snapshot_from_dict",
    "snapshot_to_dict",
    "to_jsonable",
]
