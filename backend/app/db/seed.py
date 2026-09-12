"""Development seeding: one user per role and the default configuration.

Both functions are idempotent so they can run on every dev start-up. The
well-known development accounts are never created in ``prod`` unless the
operator forces it (``python -m app.cli seed --force``); creating them in any
other environment logs a warning so the credentials are not forgotten.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from sqlalchemy.orm import Session

from app.core.errors import ConfigurationError
from app.db.records import UserRecord
from app.db.repositories.config import ConfigRepository
from app.db.repositories.users import UserRepository
from app.domain.config import SystemConfig
from app.domain.enums import Role

log = structlog.get_logger(__name__)


@dataclass(slots=True, frozen=True)
class SeedUser:
    username: str
    password: str
    role: Role
    display_name: str


#: Development accounts (never seeded in prod, see ``seed_users(..., allow=...)``).
DEV_USERS: tuple[SeedUser, ...] = (
    SeedUser("admin", "admin123", Role.ADMIN, "System Administrator"),
    SeedUser("manager", "manager123", Role.PRODUCTION_MANAGER, "Production Manager"),
    SeedUser("planner", "planner123", Role.PLANNER, "Production Planner"),
    SeedUser("supervisor", "supervisor123", Role.SUPERVISOR, "Shift Supervisor"),
    SeedUser("operator", "operator123", Role.OPERATOR, "Machine Operator"),
    SeedUser("executive", "executive123", Role.EXECUTIVE, "Executive Viewer"),
)


def seed_users(
    session: Session,
    users: tuple[SeedUser, ...] = DEV_USERS,
    *,
    environment: str = "dev",
    force: bool = False,
) -> list[UserRecord]:
    """Create any of ``users`` that do not exist yet; returns the created records.

    Refuses (``ConfigurationError``) in ``prod`` unless ``force`` is set: the
    accounts carry well-known passwords.
    """
    if environment == "prod" and not force:
        raise ConfigurationError(
            "refusing to seed the well-known development accounts in prod; "
            "pass force=True (CLI: `seed --force`) to override",
            details={"environment": environment, "usernames": [u.username for u in users]},
        )
    repo = UserRepository(session)
    created: list[UserRecord] = []
    for seed in users:
        if repo.get_by_username(seed.username) is not None:
            continue
        created.append(
            repo.create(
                username=seed.username,
                password=seed.password,
                role=seed.role,
                display_name=seed.display_name,
            )
        )
    if created:
        log.info("seeded_users", usernames=[u.username for u in created], environment=environment)
        if environment != "test":
            log.warning(
                "dev_accounts_seeded",
                environment=environment,
                usernames=[u.username for u in created],
                hint="well-known development passwords; change or disable these accounts before exposure",
            )
    return created


def seed_default_config(session: Session, created_by: str | None = None) -> bool:
    """Store ``SystemConfig()`` as version 1 when no configuration is active. Returns True if seeded."""
    repo = ConfigRepository(session)
    if repo.has_active():
        return False
    info = repo.save_new_version(
        SystemConfig(), created_by=created_by, reason="initial default configuration"
    )
    log.info("seeded_default_config", version=info.version)
    return True


def seed_all(session: Session, *, environment: str = "dev", force: bool = False) -> None:
    seed_users(session, environment=environment, force=force)
    seed_default_config(session)


__all__ = ["DEV_USERS", "SeedUser", "seed_all", "seed_default_config", "seed_users"]
