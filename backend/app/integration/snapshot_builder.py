"""Build a :class:`PlanningSnapshot` straight from a connector (no database).

This is the in-memory sync path used by tests, the CLI demo and the
simulation harness: fetch -> normalise -> assemble -> overlay production
status. The database-backed ``SyncService`` reuses the same normalizer but
persists between the steps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog

from app.core.clock import Clock
from app.core.errors import IntegrationError
from app.domain.snapshot import PlanningSnapshot
from app.integration.connector import ERPConnector, RawRecord
from app.integration.normalizer import NormalizationIssue, Normalizer
from app.integration.parsers import parse_bool

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class FetchBundle:
    """Raw records per entity from one connector round-trip."""

    records: dict[str, list[RawRecord]] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        return {entity: len(rows) for entity, rows in self.records.items()}


def fetch_all(connector: ERPConnector, since: datetime | None = None) -> FetchBundle:
    """Pull every entity from the connector, wrapping transport errors."""
    bundle = FetchBundle()
    fetchers = (
        ("customer", connector.fetch_customers),
        ("material", connector.fetch_materials),
        ("machine", connector.fetch_machines),
        ("tooling", connector.fetch_tooling),
        ("calendar", connector.fetch_calendars),
        ("order", connector.fetch_orders),
        ("operation", connector.fetch_operations),
        ("production_status", connector.fetch_production_status),
    )
    for entity, fetch in fetchers:
        try:
            bundle.records[entity] = fetch(since)
        except IntegrationError:
            raise
        except Exception as exc:  # connector failures of any kind become IntegrationError
            raise IntegrationError(
                f"connector {getattr(connector, 'name', '?')} failed fetching {entity}: {exc}",
                details={"entity": entity},
            ) from exc
    log.info("snapshot_builder.fetched", since=since.isoformat() if since else None, **bundle.counts())
    return bundle


def build_snapshot_from_records(
    bundle: FetchBundle,
    as_of: datetime,
    normalizer: Normalizer | None = None,
    source: str = "connector",
) -> tuple[PlanningSnapshot, list[NormalizationIssue]]:
    """Normalise a :class:`FetchBundle` and assemble the snapshot."""
    normalizer = normalizer or Normalizer()
    issues: list[NormalizationIssue] = []
    records = bundle.records

    customers = normalizer.normalize_customers(records.get("customer", []))
    materials = normalizer.normalize_materials(records.get("material", []))
    machines = normalizer.normalize_machines(records.get("machine", []))
    tooling = normalizer.normalize_tooling(records.get("tooling", []))
    calendars = normalizer.normalize_calendars(records.get("calendar", []))
    orders = normalizer.normalize_orders(records.get("order", []))
    operations = normalizer.normalize_operations(records.get("operation", []))
    progress = normalizer.normalize_production_status(records.get("production_status", []))
    for result in (customers, materials, machines, tooling, calendars, orders, operations, progress):
        issues.extend(result.issues)

    snapshot = PlanningSnapshot(
        as_of=as_of,
        customers={c.customer_id: c for c in customers.items},
        orders={o.order_id: o for o in orders.items},
        operations={op.operation_id: op for op in operations.items},
        machines={m.machine_id: m for m in machines.items},
        materials={m.material_id: m for m in materials.items},
        tooling={t.tooling_id: t for t in tooling.items},
        calendars={c.calendar_id: c for c in calendars.items},
        default_calendar_id=_default_calendar_id(
            records.get("calendar", []), [c.calendar_id for c in calendars.items]
        ),
        source=source,
    )

    applied = 0
    for update in sorted(progress.items, key=lambda u: (u.reported_at, u.operation_id)):
        operation = snapshot.operations.get(update.operation_id)
        if operation is None:
            continue
        update.apply(operation)
        applied += 1

    snapshot.rebuild_indexes()
    log.info(
        "snapshot_builder.built",
        source=source,
        issues=len(issues),
        progress_applied=applied,
        **snapshot.summary(),
    )
    return snapshot, issues


def build_snapshot_from_connector(
    connector: ERPConnector,
    clock: Clock,
    since: datetime | None = None,
    normalizer: Normalizer | None = None,
) -> tuple[PlanningSnapshot, list[NormalizationIssue]]:
    """Fetch everything from ``connector`` and return a snapshot as of ``clock.now()``.

    With ``since`` set only records changed after that instant are fetched, so
    the resulting snapshot is a *partial* view meant for merging by the caller.
    """
    bundle = fetch_all(connector, since)
    snapshot, issues = build_snapshot_from_records(
        bundle, as_of=clock.now(), normalizer=normalizer, source=getattr(connector, "name", "connector")
    )
    return snapshot, issues


def _default_calendar_id(raw_calendars: list[RawRecord], known_ids: list[str]) -> str | None:
    for record in raw_calendars:
        payload: dict[str, Any] = record.payload
        flag = payload.get("IS_DEFAULT")
        try:
            if flag is not None and parse_bool(flag) and record.external_id in known_ids:
                return record.external_id
        except ValueError:
            continue
    return known_ids[0] if known_ids else None


__all__ = ["FetchBundle", "build_snapshot_from_connector", "build_snapshot_from_records", "fetch_all"]
