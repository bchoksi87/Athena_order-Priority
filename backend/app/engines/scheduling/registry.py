"""Scheduler registry: algorithm name → factory (DESIGN_CONTRACT §6.4).

``default_registry(clock)`` always provides ``rule_based`` and adds ``cpsat``
when OR-Tools is importable, so the optional optimiser never breaks a
deployment that did not install the ``optimization`` extra.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import structlog

from app.core.clock import Clock
from app.core.errors import NotFoundError
from app.engines.scheduling.base import Scheduler
from app.engines.scheduling.rule_based import RuleBasedScheduler

log = structlog.get_logger(__name__)

SchedulerFactory = Callable[[Clock], Scheduler]


class SchedulerRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, SchedulerFactory] = {}

    def register(self, name: str, factory: SchedulerFactory) -> None:
        self._factories[name] = factory

    def names(self) -> list[str]:
        return sorted(self._factories)

    def __contains__(self, name: object) -> bool:
        return name in self._factories

    def __iter__(self) -> Iterator[str]:
        return iter(self.names())

    def create(self, name: str, clock: Clock) -> Scheduler:
        factory = self._factories.get(name)
        if factory is None:
            raise NotFoundError(f"unknown scheduler {name!r}", details={"available": self.names()})
        return factory(clock)


def cpsat_available() -> bool:
    try:
        import ortools.sat.python.cp_model  # noqa: F401
    except ImportError:  # pragma: no cover - depends on the environment
        return False
    return True


def default_registry(clock: Clock | None = None) -> SchedulerRegistry:
    """Registry with ``rule_based`` and, when OR-Tools is installed, ``cpsat``."""
    _ = clock  # factories take the clock at creation time; accepted for API symmetry
    registry = SchedulerRegistry()
    registry.register(RuleBasedScheduler.name, lambda c: RuleBasedScheduler(c))
    if cpsat_available():
        from app.engines.scheduling.cpsat import CpSatScheduler

        registry.register(CpSatScheduler.name, lambda c: CpSatScheduler(c))
    else:  # pragma: no cover - depends on the environment
        log.info("scheduler.cpsat_unavailable", reason="ortools not installed")
    return registry


__all__ = ["SchedulerFactory", "SchedulerRegistry", "cpsat_available", "default_registry"]
