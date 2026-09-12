"""Value parsers for ERP field normalisation.

Every parser takes a raw value (already known to be non-empty) and returns the
domain-typed value, raising :class:`ValueError` on malformed input or
:class:`UnknownCodeError` when an enum code is not recognised. The normalizer
turns those exceptions into :class:`NormalizationIssue` records; parsers never
log or swallow errors themselves.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, time
from enum import StrEnum
from typing import Any, TypeVar

from app.domain.models import Shift, TimeWindow
from app.integration.codes import LIST_SEPARATOR

E = TypeVar("E", bound=StrEnum)

Parser = Callable[[Any], Any]

_TRUE_TOKENS = frozenset({"y", "yes", "true", "t", "1", "x"})
_FALSE_TOKENS = frozenset({"n", "no", "false", "f", "0", ""})
_WEEKDAY_TOKENS: dict[str, int] = {
    "MON": 0,
    "TUE": 1,
    "WED": 2,
    "THU": 3,
    "FRI": 4,
    "SAT": 5,
    "SUN": 6,
}


class UnknownCodeError(ValueError):
    """Raised by enum parsers when a code is syntactically fine but not in the vocabulary."""

    def __init__(self, code: str, vocabulary: str) -> None:
        super().__init__(f"unknown {vocabulary} code {code!r}")
        self.code = code
        self.vocabulary = vocabulary


def is_empty(value: Any) -> bool:
    """ERP flat exports use ``None`` and ``""`` interchangeably for null."""
    return value is None or (isinstance(value, str) and value.strip() == "")


def parse_str(value: Any) -> str:
    return str(value).strip()


def parse_int(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("boolean is not an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"{value!r} is not an integer")
        return int(value)
    return int(str(value).strip())


def parse_float(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean is not a number")
    if isinstance(value, int | float):
        return float(value)
    return float(str(value).strip().replace(",", ""))


def parse_percent(value: Any) -> float:
    """``"110.5"`` (percent) -> ``1.105``. Non-negative, no upper bound (efficiency may exceed 100%)."""
    number = parse_float(value)
    if number < 0:
        raise ValueError(f"percentage {number!r} is negative")
    return round(number / 100.0, 12)


def parse_percent_ratio(value: Any) -> float:
    """``"37.5"`` (percent) -> ``0.375``; must lie in [0, 100]."""
    number = parse_float(value)
    if number < 0 or number > 100:
        raise ValueError(f"percentage {number!r} out of range 0..100")
    return round(number / 100.0, 12)


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    token = str(value).strip().lower()
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    raise ValueError(f"{value!r} is not a boolean flag")


def parse_datetime(value: Any) -> datetime:
    """ISO-8601 string (date or datetime, with or without offset) -> aware UTC datetime."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            parsed = datetime.combine(date.fromisoformat(text), time.min)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip()[:10])


def parse_time(value: Any) -> time:
    if isinstance(value, time):
        return value
    return time.fromisoformat(str(value).strip())


def parse_str_list(value: Any) -> list[str]:
    if isinstance(value, list | tuple | set | frozenset):
        return [str(v).strip() for v in value if not is_empty(v)]
    return [part.strip() for part in str(value).split(LIST_SEPARATOR) if part.strip()]


def parse_str_set(value: Any) -> set[str]:
    return set(parse_str_list(value))


def parse_date_list(value: Any) -> list[date]:
    return [parse_date(item) for item in parse_str_list(value)]


def parse_float_map(value: Any) -> dict[str, float]:
    """``"M1=3.5;M2=4"`` or ``{"M1": 3.5}`` -> ``{"M1": 3.5, "M2": 4.0}``."""
    if isinstance(value, Mapping):
        return {str(k): parse_float(v) for k, v in value.items()}
    out: dict[str, float] = {}
    for item in parse_str_list(value):
        key, sep, number = item.partition("=")
        if not sep:
            raise ValueError(f"expected KEY=VALUE, got {item!r}")
        out[key.strip()] = parse_float(number)
    return out


def parse_size_mm(value: Any) -> tuple[float, float, float]:
    """``"500x400x300"`` -> ``(500.0, 400.0, 300.0)``."""
    if isinstance(value, list | tuple) and len(value) == 3:
        return (parse_float(value[0]), parse_float(value[1]), parse_float(value[2]))
    parts = [p for p in str(value).lower().replace("\u00d7", "x").split("x") if p.strip()]
    if len(parts) != 3:
        raise ValueError(f"expected LxWxH, got {value!r}")
    return (parse_float(parts[0]), parse_float(parts[1]), parse_float(parts[2]))


def parse_windows(value: Any) -> list[TimeWindow]:
    """List of ``{"START": iso, "END": iso, "REASON": str}`` -> ``TimeWindow`` list."""
    if not isinstance(value, list | tuple):
        raise ValueError("expected a list of windows")
    windows: list[TimeWindow] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("window must be a mapping")
        start = parse_datetime(item.get("START") or item.get("start"))
        end = parse_datetime(item.get("END") or item.get("end"))
        reason = str(item.get("REASON") or item.get("reason") or "")
        windows.append(TimeWindow(start, end, reason))
    return windows


def parse_weekdays(value: Any) -> tuple[int, ...]:
    """``"MON,TUE,WED"`` or ``[0, 1, 2]`` -> ``(0, 1, 2)``."""
    if isinstance(value, list | tuple):
        return tuple(sorted({parse_int(v) for v in value}))
    days: set[int] = set()
    for token in str(value).replace(LIST_SEPARATOR, ",").split(","):
        token = token.strip().upper()[:3]
        if not token:
            continue
        if token.isdigit():
            days.add(int(token))
        elif token in _WEEKDAY_TOKENS:
            days.add(_WEEKDAY_TOKENS[token])
        else:
            raise ValueError(f"unknown weekday {token!r}")
    if any(d < 0 or d > 6 for d in days):
        raise ValueError("weekday out of range")
    return tuple(sorted(days))


def parse_shifts(value: Any) -> list[Shift]:
    """List of ``{"NAME", "START": "06:00", "END": "14:00", "DAYS": "MON,..."}`` -> ``Shift`` list."""
    if not isinstance(value, list | tuple):
        raise ValueError("expected a list of shifts")
    shifts: list[Shift] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("shift must be a mapping")
        name = str(item.get("NAME") or item.get("name") or f"Shift {len(shifts) + 1}")
        start = parse_time(item.get("START") or item.get("start"))
        end = parse_time(item.get("END") or item.get("end"))
        days_raw = item.get("DAYS", item.get("days"))
        weekdays = parse_weekdays(days_raw) if not is_empty(days_raw) else (0, 1, 2, 3, 4)
        shifts.append(Shift(name=name, start=start, end=end, weekdays=weekdays))
    return shifts


def make_enum_parser(codes: Mapping[str, E], vocabulary: str) -> Callable[[Any], E]:
    """Build a parser that maps ERP codes (or enum values) to ``E``.

    Accepts the ERP code (case-insensitive) *or* the enum's own value so that
    connectors which already speak the domain vocabulary work unchanged.
    """
    by_code = {code.upper(): member for code, member in codes.items()}
    by_value = {member.value.upper(): member for member in codes.values()}

    def parse(value: Any) -> E:
        if isinstance(value, StrEnum) and value in by_value.values():
            return by_value[value.value.upper()]
        token = str(value).strip().upper()
        member = by_code.get(token) or by_value.get(token)
        if member is None:
            raise UnknownCodeError(token, vocabulary)
        return member

    return parse


__all__ = [
    "Parser",
    "UnknownCodeError",
    "is_empty",
    "make_enum_parser",
    "parse_bool",
    "parse_date",
    "parse_date_list",
    "parse_datetime",
    "parse_float",
    "parse_float_map",
    "parse_int",
    "parse_percent",
    "parse_percent_ratio",
    "parse_shifts",
    "parse_size_mm",
    "parse_str",
    "parse_str_list",
    "parse_str_set",
    "parse_time",
    "parse_weekdays",
    "parse_windows",
]
