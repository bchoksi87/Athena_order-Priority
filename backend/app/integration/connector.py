"""ERP connector abstraction (docs/DESIGN_CONTRACT.md §8).

The ERP/MES is an unknown external system. Connectors are *read-only* sources
of :class:`RawRecord` objects; the normalizer converts them to the domain
model. Nothing outside ``app/integration`` should ever see a raw record.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

import structlog

from app.core.clock import Clock, ensure_utc
from app.core.errors import NotFoundError

log = structlog.get_logger(__name__)

#: Entity names a connector may serve, in dependency order (masters first).
ENTITY_NAMES: tuple[str, ...] = (
    "customer",
    "material",
    "machine",
    "tooling",
    "calendar",
    "order",
    "operation",
    "production_status",
)


@dataclass(slots=True, frozen=True)
class RawRecord:
    """One entity row exactly as the ERP exposed it (flat dict, ERP-native keys)."""

    entity: str
    external_id: str
    payload: dict[str, Any]
    updated_at: datetime
    source: str

    def __post_init__(self) -> None:
        if self.updated_at.tzinfo is None:
            raise ValueError("RawRecord.updated_at must be timezone-aware")


@dataclass(slots=True)
class ConnectorCapabilities:
    """Which *normalised* fields a connector can populate per entity.

    ``fields`` maps entity name -> domain attribute names (e.g. ``"order" ->
    ["order_id", "customer_id", "quantity", ...]``). :mod:`app.integration.capabilities`
    compares this against what the engines require.
    """

    connector_name: str
    fields: dict[str, list[str]] = field(default_factory=dict)
    supports_incremental: bool = False
    supports_webhooks: bool = False
    notes: dict[str, str] = field(default_factory=dict)

    def has(self, entity: str, field_name: str) -> bool:
        return field_name in self.fields.get(entity, ())


@dataclass(slots=True)
class ConnectorHealth:
    connector_name: str
    healthy: bool
    checked_at: datetime
    latency_ms: float | None = None
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ERPConnector(Protocol):
    """Read-only access to ERP/MES data. ``since`` requests an incremental slice."""

    name: str

    def fetch_customers(self, since: datetime | None = None) -> list[RawRecord]: ...

    def fetch_orders(self, since: datetime | None = None) -> list[RawRecord]: ...

    def fetch_operations(self, since: datetime | None = None) -> list[RawRecord]: ...

    def fetch_machines(self, since: datetime | None = None) -> list[RawRecord]: ...

    def fetch_materials(self, since: datetime | None = None) -> list[RawRecord]: ...

    def fetch_tooling(self, since: datetime | None = None) -> list[RawRecord]: ...

    def fetch_calendars(self, since: datetime | None = None) -> list[RawRecord]: ...

    def fetch_production_status(self, since: datetime | None = None) -> list[RawRecord]: ...

    def capabilities(self) -> ConnectorCapabilities: ...

    def health(self) -> ConnectorHealth: ...


ConnectorFactory = Callable[..., ERPConnector]


class ConnectorRegistry:
    """Name -> factory registry so the connector is chosen by configuration.

    Instances are created explicitly (``ConnectorRegistry.default()``) rather
    than kept as module state; callers own the registry they build.
    """

    def __init__(self) -> None:
        self._factories: dict[str, ConnectorFactory] = {}

    def register(self, name: str, factory: ConnectorFactory) -> None:
        log.debug("connector.registry.register", name=name)
        self._factories[name] = factory

    def names(self) -> list[str]:
        return sorted(self._factories)

    def create(self, name: str, clock: Clock, **options: Any) -> ERPConnector:
        factory = self._factories.get(name)
        if factory is None:
            raise NotFoundError(
                f"no ERP connector registered as {name!r}", details={"available": self.names()}
            )
        connector = factory(clock=clock, **options)
        log.info("connector.registry.create", name=name, options=sorted(options))
        return connector

    @classmethod
    def default(cls) -> ConnectorRegistry:
        """Registry with the built-in ``mock`` connector (synthetic data)."""
        registry = cls()
        registry.register("mock", _create_mock_connector)
        return registry


def _create_mock_connector(clock: Clock, **options: Any) -> ERPConnector:
    # Imported lazily: the mock connector depends on the synthetic package.
    from app.integration.mock_connector import MockERPConnector
    from synthetic.generator import SyntheticDataGenerator

    dataset = options.get("dataset")
    if dataset is None:
        # Anchor the synthetic plant to the current day (UTC midnight) so that
        # "due today / overdue" reflect the real clock and the dataset stays
        # stable for every sync run within the same day.
        as_of = options.get("as_of")
        if as_of is None:
            as_of = ensure_utc(clock.now()).replace(hour=0, minute=0, second=0, microsecond=0)
        generator = SyntheticDataGenerator(
            seed=int(options.get("seed", 42)),
            scale=options.get("scale", "medium"),
            as_of=as_of,
            dq_defect_ratio=float(options.get("dq_defect_ratio", 0.03)),
        )
        dataset = generator.generate()
    return MockERPConnector(dataset, clock)


def count_by_entity(records: Mapping[str, list[RawRecord]]) -> dict[str, int]:
    """Convenience for reconciliation: entity -> number of raw records."""
    return {entity: len(rows) for entity, rows in records.items()}


__all__ = [
    "ENTITY_NAMES",
    "ConnectorCapabilities",
    "ConnectorFactory",
    "ConnectorHealth",
    "ConnectorRegistry",
    "ERPConnector",
    "RawRecord",
    "count_by_entity",
]
