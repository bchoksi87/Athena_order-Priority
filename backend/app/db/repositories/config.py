"""ConfigRepository: versioned :class:`SystemConfig` storage.

Every save creates a new ``system_configs`` version and mirrors the embedded
priority profile / scheduling config into their own tables under the same
version number, so results can reference ``(profile_id, profile_version)``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import func, select

from app.core.errors import ConfigurationError, NotFoundError, ValidationError
from app.core.ids import new_id
from app.db.models import PriorityProfileRow, SchedulingConfigRow, SystemConfigRow
from app.db.records import ConfigVersionInfo
from app.db.repositories.base import Repository
from app.domain.config import SystemConfig


class ConfigRepository(Repository):
    def get_active(self) -> SystemConfig:
        row = self._active_row()
        if row is None:
            raise ConfigurationError("no active system configuration; seed the database first")
        return _parse(row.payload)

    def get_active_info(self) -> ConfigVersionInfo | None:
        row = self._active_row()
        return _info(row) if row else None

    def has_active(self) -> bool:
        return self._active_row() is not None

    def get_version(self, version: int) -> SystemConfig:
        row = self._version_row(version)
        if row is None:
            raise NotFoundError(f"configuration version {version} not found", details={"version": version})
        return _parse(row.payload)

    def list_versions(self, limit: int = 100) -> list[ConfigVersionInfo]:
        stmt = select(SystemConfigRow).order_by(SystemConfigRow.version.desc()).limit(limit)
        return [_info(r) for r in self._session.execute(stmt).scalars()]

    def next_version(self) -> int:
        current = self._session.execute(select(func.max(SystemConfigRow.version))).scalar_one()
        return int(current or 0) + 1

    def save_new_version(
        self,
        config: SystemConfig,
        *,
        created_by: str | None = None,
        reason: str | None = None,
        activate: bool = True,
        at: datetime | None = None,
    ) -> ConfigVersionInfo:
        """Persist ``config`` as the next version (its embedded versions are aligned)."""
        version = self.next_version()
        aligned = config.model_copy(
            update={
                "priority_profile": config.priority_profile.model_copy(update={"version": version}),
                "scheduling": config.scheduling.model_copy(update={"version": version}),
            },
            deep=True,
        )
        payload = aligned.model_dump(mode="json")
        if activate:
            for row in self._session.execute(
                select(SystemConfigRow).where(SystemConfigRow.is_active.is_(True))
            ).scalars():
                row.is_active = False
            for prow in self._session.execute(
                select(PriorityProfileRow).where(PriorityProfileRow.is_active.is_(True))
            ).scalars():
                prow.is_active = False
            for srow in self._session.execute(
                select(SchedulingConfigRow).where(SchedulingConfigRow.is_active.is_(True))
            ).scalars():
                srow.is_active = False

        system_row = SystemConfigRow(
            config_id=new_id("cfg"),
            version=version,
            is_active=activate,
            payload=payload,
            created_by=created_by,
            reason=reason,
        )
        self._session.add(system_row)
        self._session.add(
            PriorityProfileRow(
                row_id=new_id("pp"),
                profile_id=aligned.priority_profile.profile_id,
                version=version,
                name=aligned.priority_profile.name,
                is_active=activate,
                payload=payload["priority_profile"],
                system_config_version=version,
                created_by=created_by,
            )
        )
        self._session.add(
            SchedulingConfigRow(
                row_id=new_id("sc"),
                config_id=aligned.scheduling.config_id,
                version=version,
                name=aligned.scheduling.name,
                is_active=activate,
                payload=payload["scheduling"],
                system_config_version=version,
                created_by=created_by,
            )
        )
        self._flush()
        return _info(system_row)

    def activate_version(self, version: int) -> ConfigVersionInfo:
        """Make an older version active again (rollback) without creating a copy."""
        target = self._version_row(version)
        if target is None:
            raise NotFoundError(f"configuration version {version} not found", details={"version": version})
        for row in self._session.execute(select(SystemConfigRow)).scalars():
            row.is_active = row.version == version
        for prow in self._session.execute(select(PriorityProfileRow)).scalars():
            prow.is_active = prow.system_config_version == version
        for srow in self._session.execute(select(SchedulingConfigRow)).scalars():
            srow.is_active = srow.system_config_version == version
        self._flush()
        return _info(target)

    # -------------------------------------------------------------- helpers
    def _active_row(self) -> SystemConfigRow | None:
        stmt = (
            select(SystemConfigRow)
            .where(SystemConfigRow.is_active.is_(True))
            .order_by(SystemConfigRow.version.desc())
            .limit(1)
        )
        return self._session.execute(stmt).scalar_one_or_none()

    def _version_row(self, version: int) -> SystemConfigRow | None:
        stmt = select(SystemConfigRow).where(SystemConfigRow.version == version)
        return self._session.execute(stmt).scalar_one_or_none()


def _parse(payload: dict[str, object]) -> SystemConfig:
    try:
        return SystemConfig.model_validate(payload)
    except PydanticValidationError as exc:
        raise ValidationError("stored configuration is invalid", details={"errors": exc.errors()}) from exc


def _info(row: SystemConfigRow) -> ConfigVersionInfo:
    payload = row.payload or {}
    profile = payload.get("priority_profile", {}) if isinstance(payload, dict) else {}
    scheduling = payload.get("scheduling", {}) if isinstance(payload, dict) else {}
    return ConfigVersionInfo(
        config_id=row.config_id,
        version=row.version,
        is_active=row.is_active,
        created_by=row.created_by,
        reason=row.reason,
        created_at=row.created_at,
        profile_id=str(profile.get("profile_id", "")),
        scheduling_config_id=str(scheduling.get("config_id", "")),
    )


__all__ = ["ConfigRepository"]
