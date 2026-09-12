"""ConfigService: versioned rule configuration (spec Phase 14, contract §10).

Each update validates the section (Pydantic), stores a *new* ``system_configs``
version through :class:`ConfigRepository` (which aligns the embedded profile /
scheduling version numbers), and writes an audit row whose previous/new values
contain only the keys that changed. ``activate_version`` rolls back to an
earlier version without copying it; ``preview_profile`` runs the priority
engine twice on the live snapshot to answer "what would change?".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.errors import ConflictError, ValidationError
from app.core.security import CurrentUser
from app.db.records import ConfigVersionInfo
from app.db.repositories.config import ConfigRepository
from app.domain.config import (
    AlertConfig,
    DataQualityConfig,
    PriorityProfile,
    ReplanningConfig,
    SchedulingConfig,
    SystemConfig,
)
from app.engines.constraints.registry import default_constraint_engine
from app.engines.priority.context import PriorityContextBuilder
from app.engines.priority.engine import PriorityEngine
from app.engines.priority.preview import ProfileComparison, compare_profiles
from app.engines.priority.registry import default_factors
from app.services.audit_service import ENTITY_CONFIG, AuditService, json_diff
from app.services.base import Service, actor_id, require_reason
from app.services.snapshot_service import SnapshotService

log = structlog.get_logger(__name__)

SECTION_PRIORITY = "priority_profile"
SECTION_SCHEDULING = "scheduling"
SECTION_REPLANNING = "replanning"
SECTION_ALERTS = "alerts"
SECTION_DATA_QUALITY = "data_quality"

MAX_PREVIEW_TOP_N = 500


@dataclass(slots=True)
class ConfigUpdate:
    info: ConfigVersionInfo
    config: SystemConfig
    previous_version: int | None
    changed_previous: dict[str, Any]
    changed_new: dict[str, Any]


class ConfigService(Service):
    def __init__(
        self,
        session: Session,
        clock: Clock,
        audit: AuditService,
        snapshots: SnapshotService | None = None,
    ) -> None:
        super().__init__(session, clock)
        self._audit = audit
        self._snapshots = snapshots or SnapshotService(session, clock)
        self._repo = ConfigRepository(session)

    # ---------------------------------------------------------------- reads
    def get_active(self) -> SystemConfig:
        return self._repo.get_active()

    def get_active_info(self) -> ConfigVersionInfo | None:
        return self._repo.get_active_info()

    def list_versions(self, limit: int = 100) -> list[ConfigVersionInfo]:
        return self._repo.list_versions(limit=limit)

    def get_version(self, version: int) -> SystemConfig:
        return self._repo.get_version(version)

    def get_version_info(self, version: int) -> ConfigVersionInfo:
        for info in self._repo.list_versions(limit=10_000):
            if info.version == version:
                return info
        self._repo.get_version(version)  # raises NotFoundError
        raise ConflictError(f"configuration version {version} has no version record")

    # -------------------------------------------------------------- updates
    def update_priority_profile(
        self, profile: PriorityProfile, user: CurrentUser | str, reason: str
    ) -> ConfigUpdate:
        return self._save_section(SECTION_PRIORITY, profile, user, reason)

    def update_scheduling_config(
        self, config: SchedulingConfig, user: CurrentUser | str, reason: str
    ) -> ConfigUpdate:
        return self._save_section(SECTION_SCHEDULING, config, user, reason)

    def update_replanning(
        self, config: ReplanningConfig, user: CurrentUser | str, reason: str
    ) -> ConfigUpdate:
        return self._save_section(SECTION_REPLANNING, config, user, reason)

    def update_alerts(self, config: AlertConfig, user: CurrentUser | str, reason: str) -> ConfigUpdate:
        return self._save_section(SECTION_ALERTS, config, user, reason)

    def update_data_quality(
        self, config: DataQualityConfig, user: CurrentUser | str, reason: str
    ) -> ConfigUpdate:
        return self._save_section(SECTION_DATA_QUALITY, config, user, reason)

    def activate_version(self, version: int, user: CurrentUser | str, reason: str) -> ConfigUpdate:
        """Roll back (or forward) to an existing version; audited as a full diff."""
        reason = require_reason(reason)
        previous_info = self._repo.get_active_info()
        if previous_info is not None and previous_info.version == version:
            raise ConflictError(f"configuration version {version} is already active")
        previous = self._repo.get_active() if previous_info else None
        info = self._repo.activate_version(version)
        config = self._repo.get_version(version)
        changed_previous, changed_new = json_diff(previous, config)
        self._audit.record(
            user,
            ENTITY_CONFIG,
            info.config_id,
            "config.activate_version",
            {"version": previous_info.version if previous_info else None, "changes": changed_previous},
            {"version": version, "changes": changed_new},
            reason,
            {"previous_version": previous_info.version if previous_info else None, "version": version},
        )
        log.info("config.activated", version=version, user_id=actor_id(user))
        return ConfigUpdate(
            info, config, previous_info.version if previous_info else None, changed_previous, changed_new
        )

    # -------------------------------------------------------------- preview
    def preview_profile(self, candidate: PriorityProfile, top_n: int = 50) -> ProfileComparison:
        """Evaluate the active profile vs ``candidate`` on the live snapshot (spec Phase 14)."""
        if top_n < 1 or top_n > MAX_PREVIEW_TOP_N:
            raise ValidationError(f"top_n must be in 1..{MAX_PREVIEW_TOP_N}", details={"top_n": top_n})
        active = self._repo.get_active()
        snapshot = self._snapshots.load_snapshot()
        builder = PriorityContextBuilder(
            constraint_engine=default_constraint_engine(active.scheduling), clock=self._clock
        )
        engine = PriorityEngine(default_factors(), self._clock, builder=builder)
        comparison = compare_profiles(
            snapshot,
            active.priority_profile,
            candidate,
            engine,
            top_n=top_n,
            customer_rules=snapshot.customer_rules,
        )
        log.info(
            "config.preview",
            profile=candidate.profile_id,
            entered=len(comparison.entered_top_n),
            left=len(comparison.left_top_n),
            top_n=top_n,
        )
        return comparison

    # ------------------------------------------------------------ internals
    def _save_section(self, section: str, value: Any, user: CurrentUser | str, reason: str) -> ConfigUpdate:
        reason = require_reason(reason)
        previous_info = self._repo.get_active_info()
        previous = self._repo.get_active()
        candidate = previous.model_copy(update={section: value}, deep=True)
        try:
            candidate = SystemConfig.model_validate(candidate.model_dump(mode="json"))
        except ValueError as exc:  # pydantic ValidationError is a ValueError
            raise ValidationError("configuration is invalid", details={"errors": str(exc)}) from exc
        before = getattr(previous, section).model_dump(mode="json", exclude={"version"})
        after = getattr(candidate, section).model_dump(mode="json", exclude={"version"})
        if before == after:
            raise ConflictError(f"{section} is unchanged; nothing to save", details={"section": section})
        info = self._repo.save_new_version(
            candidate, created_by=actor_id(user), reason=reason, activate=True, at=self.now()
        )
        saved = self._repo.get_version(info.version)
        changed_previous, changed_new = json_diff(previous, saved)
        self._audit.record(
            user,
            ENTITY_CONFIG,
            info.config_id,
            f"config.update_{section}",
            {"version": previous_info.version if previous_info else None, "changes": changed_previous},
            {"version": info.version, "changes": changed_new},
            reason,
            {
                "section": section,
                "previous_version": previous_info.version if previous_info else None,
                "version": info.version,
                "changed_keys": sorted(changed_new),
            },
        )
        log.info("config.saved", section=section, version=info.version, user_id=actor_id(user))
        return ConfigUpdate(
            info, saved, previous_info.version if previous_info else None, changed_previous, changed_new
        )


__all__ = [
    "SECTION_ALERTS",
    "SECTION_DATA_QUALITY",
    "SECTION_PRIORITY",
    "SECTION_REPLANNING",
    "SECTION_SCHEDULING",
    "ConfigService",
    "ConfigUpdate",
]
