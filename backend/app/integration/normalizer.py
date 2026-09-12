"""ERP raw records -> Normalized Manufacturing Data Model.

The normalizer never raises on bad data: every problem becomes a
:class:`NormalizationIssue` and the record is skipped (missing required
field) or degraded (optional field left at its default). Unknown enum codes
map to the field's declared default and are reported as ``unknown_code``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Generic, TypeVar

import structlog

from app.domain.enums import OperationStatus
from app.domain.models import CalendarSpec, Customer, Machine, Material, Operation, Order, Tooling
from app.integration.codes import compose_order_id
from app.integration.connector import RawRecord
from app.integration.field_maps import FIELD_MAPS, OMIT, FieldMap
from app.integration.parsers import UnknownCodeError, is_empty, parse_int

log = structlog.get_logger(__name__)

T = TypeVar("T")


class NormalizationIssueCode(StrEnum):
    MISSING_REQUIRED = "missing_required"
    INVALID_VALUE = "invalid_value"
    UNKNOWN_CODE = "unknown_code"
    DUPLICATE_ID = "duplicate_id"
    MALFORMED_RECORD = "malformed_record"
    WRONG_ENTITY = "wrong_entity"


@dataclass(slots=True)
class NormalizationIssue:
    entity: str
    external_id: str
    field: str | None
    code: NormalizationIssueCode
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity": self.entity,
            "external_id": self.external_id,
            "field": self.field,
            "code": self.code.value,
            "message": self.message,
        }


@dataclass(slots=True)
class NormalizationResult(Generic[T]):
    entity: str
    items: list[T] = field(default_factory=list)
    issues: list[NormalizationIssue] = field(default_factory=list)
    records_in: int = 0

    @property
    def skipped(self) -> int:
        return max(0, self.records_in - len(self.items))

    def issues_with(self, code: NormalizationIssueCode) -> list[NormalizationIssue]:
        return [i for i in self.issues if i.code == code]


@dataclass(slots=True)
class ProductionStatusUpdate:
    """One progress report from the shop floor, applied onto an :class:`Operation`."""

    operation_id: str
    reported_at: datetime
    operation_status: OperationStatus = OperationStatus.PENDING
    completed_quantity: float | None = None
    machine_id: str | None = None
    actual_start: datetime | None = None
    actual_end: datetime | None = None
    external_order_ref: str | None = None
    order_line_id: str | None = None

    def apply(self, operation: Operation) -> None:
        operation.operation_status = self.operation_status
        if self.completed_quantity is not None:
            operation.completed_quantity = self.completed_quantity
        if self.machine_id is not None:
            operation.machine_id = self.machine_id
        if self.actual_start is not None:
            operation.actual_start = self.actual_start
        if self.actual_end is not None:
            operation.actual_end = self.actual_end
        operation.attributes["last_progress_report_at"] = self.reported_at.isoformat()


class Normalizer:
    """Applies the declarative :mod:`app.integration.field_maps` to raw records."""

    def __init__(self, field_maps: Mapping[str, Sequence[FieldMap]] | None = None) -> None:
        self._maps: dict[str, tuple[FieldMap, ...]] = {
            entity: tuple(maps) for entity, maps in (field_maps or FIELD_MAPS).items()
        }

    # ------------------------------------------------------------ public API
    def normalize(self, entity: str, records: Iterable[RawRecord]) -> NormalizationResult[Any]:
        handlers: dict[str, Callable[[Iterable[RawRecord]], NormalizationResult[Any]]] = {
            "customer": self.normalize_customers,
            "order": self.normalize_orders,
            "operation": self.normalize_operations,
            "machine": self.normalize_machines,
            "material": self.normalize_materials,
            "tooling": self.normalize_tooling,
            "calendar": self.normalize_calendars,
            "production_status": self.normalize_production_status,
        }
        handler = handlers.get(entity)
        if handler is None:
            raise KeyError(f"no normalizer for entity {entity!r}")
        return handler(records)

    def normalize_customers(self, records: Iterable[RawRecord]) -> NormalizationResult[Customer]:
        return self._normalize_simple("customer", records, Customer, "customer_id")

    def normalize_machines(self, records: Iterable[RawRecord]) -> NormalizationResult[Machine]:
        return self._normalize_simple("machine", records, Machine, "machine_id")

    def normalize_materials(self, records: Iterable[RawRecord]) -> NormalizationResult[Material]:
        return self._normalize_simple("material", records, Material, "material_id")

    def normalize_tooling(self, records: Iterable[RawRecord]) -> NormalizationResult[Tooling]:
        return self._normalize_simple("tooling", records, Tooling, "tooling_id")

    def normalize_calendars(self, records: Iterable[RawRecord]) -> NormalizationResult[CalendarSpec]:
        return self._normalize_simple("calendar", records, CalendarSpec, "calendar_id", tag=False)

    def normalize_orders(self, records: Iterable[RawRecord]) -> NormalizationResult[Order]:
        """Orders: ``order_id`` is composed from ORDER_NO + LINE_NO.

        Duplicate order/line references are *kept* (suffixed ``-DUPn`` and tagged
        with ``attributes["duplicate_of"]``) so the Data Quality Engine can report
        them; the duplication is also recorded here as ``duplicate_id``.
        """
        result: NormalizationResult[Order] = NormalizationResult(entity="order")
        seen: dict[str, int] = {}
        for record in records:
            result.records_in += 1
            kwargs = self._map_record("order", record, result.issues)
            if kwargs is None:
                continue
            order_id = self._compose_order_id(record, kwargs, result.issues)
            if order_id is None:
                continue
            if order_id in seen:
                seen[order_id] += 1
                original = order_id
                order_id = f"{original}-DUP{seen[original]}"
                result.issues.append(
                    NormalizationIssue(
                        "order",
                        record.external_id,
                        "order_id",
                        NormalizationIssueCode.DUPLICATE_ID,
                        f"order line {original} appears more than once; kept as {order_id}",
                    )
                )
                kwargs.setdefault("attributes", {})["duplicate_of"] = original
            else:
                seen[order_id] = 0
            order = self._construct(Order, {"order_id": order_id, **kwargs}, record, result.issues)
            if order is not None:
                order.attributes["erp_row_id"] = record.external_id
                result.items.append(order)
        self._log(result)
        return result

    def normalize_operations(self, records: Iterable[RawRecord]) -> NormalizationResult[Operation]:
        result: NormalizationResult[Operation] = NormalizationResult(entity="operation")
        seen: set[str] = set()
        for record in records:
            result.records_in += 1
            kwargs = self._map_record("operation", record, result.issues)
            if kwargs is None:
                continue
            order_id = self._compose_order_id(record, kwargs, result.issues, entity="operation")
            if order_id is None:
                continue
            operation_id = kwargs["operation_id"]
            if operation_id in seen:
                result.issues.append(
                    NormalizationIssue(
                        "operation",
                        record.external_id,
                        "operation_id",
                        NormalizationIssueCode.DUPLICATE_ID,
                        f"operation {operation_id} appears more than once; later copy skipped",
                    )
                )
                continue
            seen.add(operation_id)
            op = self._construct(Operation, {"order_id": order_id, **kwargs}, record, result.issues)
            if op is not None:
                op.attributes["erp_row_id"] = record.external_id
                result.items.append(op)
        self._log(result)
        return result

    def normalize_production_status(
        self, records: Iterable[RawRecord]
    ) -> NormalizationResult[ProductionStatusUpdate]:
        result: NormalizationResult[ProductionStatusUpdate] = NormalizationResult(entity="production_status")
        for record in records:
            result.records_in += 1
            kwargs = self._map_record("production_status", record, result.issues)
            if kwargs is None:
                continue
            update = self._construct(ProductionStatusUpdate, kwargs, record, result.issues)
            if update is not None:
                result.items.append(update)
        self._log(result)
        return result

    # ------------------------------------------------------------ internals
    def _normalize_simple(
        self,
        entity: str,
        records: Iterable[RawRecord],
        cls: type[T],
        id_field: str,
        *,
        tag: bool = True,
    ) -> NormalizationResult[T]:
        result: NormalizationResult[T] = NormalizationResult(entity=entity)
        seen: set[str] = set()
        for record in records:
            result.records_in += 1
            kwargs = self._map_record(entity, record, result.issues)
            if kwargs is None:
                continue
            identifier = kwargs[id_field]
            if identifier in seen:
                result.issues.append(
                    NormalizationIssue(
                        entity,
                        record.external_id,
                        id_field,
                        NormalizationIssueCode.DUPLICATE_ID,
                        f"{entity} {identifier} appears more than once; later copy skipped",
                    )
                )
                continue
            seen.add(identifier)
            item = self._construct(cls, kwargs, record, result.issues)
            if item is None:
                continue
            if tag:
                getattr(item, "attributes")["erp_row_id"] = record.external_id  # noqa: B009
            result.items.append(item)
        self._log(result)
        return result

    def _map_record(
        self, entity: str, record: RawRecord, issues: list[NormalizationIssue]
    ) -> dict[str, Any] | None:
        if record.entity != entity:
            issues.append(
                NormalizationIssue(
                    entity,
                    record.external_id,
                    None,
                    NormalizationIssueCode.WRONG_ENTITY,
                    f"expected a {entity} record, got {record.entity!r}",
                )
            )
            return None
        payload = record.payload
        if not isinstance(payload, Mapping):
            issues.append(
                NormalizationIssue(
                    entity,
                    record.external_id,
                    None,
                    NormalizationIssueCode.MALFORMED_RECORD,
                    "payload is not a mapping",
                )
            )
            return None
        kwargs: dict[str, Any] = {}
        usable = True
        for fmap in self._maps.get(entity, ()):
            raw = payload.get(fmap.source)
            if is_empty(raw):
                if fmap.required:
                    usable = False
                    issues.append(
                        NormalizationIssue(
                            entity,
                            record.external_id,
                            fmap.target,
                            NormalizationIssueCode.MISSING_REQUIRED,
                            f"required field {fmap.source} is missing",
                        )
                    )
                else:
                    self._apply_fallback(kwargs, fmap)
                continue
            try:
                kwargs[fmap.target] = fmap.parser(raw)
            except UnknownCodeError as exc:
                issues.append(
                    NormalizationIssue(
                        entity,
                        record.external_id,
                        fmap.target,
                        NormalizationIssueCode.UNKNOWN_CODE,
                        f"{fmap.source}={exc.code!r} is not a known {exc.vocabulary} code; default applied",
                    )
                )
                self._apply_fallback(kwargs, fmap)
            except (ValueError, TypeError, AttributeError) as exc:
                issues.append(
                    NormalizationIssue(
                        entity,
                        record.external_id,
                        fmap.target,
                        NormalizationIssueCode.INVALID_VALUE,
                        f"{fmap.source}={raw!r} could not be parsed: {exc}",
                    )
                )
                if fmap.required:
                    usable = False
                else:
                    self._apply_fallback(kwargs, fmap)
        return kwargs if usable else None

    @staticmethod
    def _apply_fallback(kwargs: dict[str, Any], fmap: FieldMap) -> None:
        fallback = fmap.fallback()
        if fallback is not OMIT:
            kwargs[fmap.target] = fallback

    @staticmethod
    def _compose_order_id(
        record: RawRecord,
        kwargs: dict[str, Any],
        issues: list[NormalizationIssue],
        entity: str = "order",
    ) -> str | None:
        order_no = kwargs.get("external_order_ref")
        line_raw = kwargs.get("order_line_id")
        if entity == "operation":
            kwargs.pop("external_order_ref", None)
            kwargs.pop("order_line_id", None)
        try:
            line_no = parse_int(line_raw)
        except (ValueError, TypeError):
            issues.append(
                NormalizationIssue(
                    entity,
                    record.external_id,
                    "order_line_id",
                    NormalizationIssueCode.INVALID_VALUE,
                    f"LINE_NO={line_raw!r} is not an integer",
                )
            )
            return None
        if not order_no:
            return None
        return compose_order_id(str(order_no), line_no)

    @staticmethod
    def _construct(
        cls: type[T], kwargs: dict[str, Any], record: RawRecord, issues: list[NormalizationIssue]
    ) -> T | None:
        try:
            return cls(**kwargs)
        except (TypeError, ValueError) as exc:
            issues.append(
                NormalizationIssue(
                    record.entity,
                    record.external_id,
                    None,
                    NormalizationIssueCode.MALFORMED_RECORD,
                    f"could not build {cls.__name__}: {exc}",
                )
            )
            return None

    @staticmethod
    def _log(result: NormalizationResult[Any]) -> None:
        log.info(
            "normalizer.done",
            entity=result.entity,
            records_in=result.records_in,
            items=len(result.items),
            skipped=result.skipped,
            issues=len(result.issues),
        )


__all__ = [
    "NormalizationIssue",
    "NormalizationIssueCode",
    "NormalizationResult",
    "Normalizer",
    "ProductionStatusUpdate",
]
