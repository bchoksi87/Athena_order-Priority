"""Idempotent dev seeding."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.core.errors import ConfigurationError
from app.db.repositories.config import ConfigRepository
from app.db.repositories.users import UserRepository
from app.db.seed import DEV_USERS, seed_all, seed_default_config, seed_users
from app.domain.config import SystemConfig
from app.domain.enums import Role

pytestmark = pytest.mark.unit


def test_seed_users_creates_one_per_role_once(session: Session) -> None:
    created = seed_users(session)
    assert {u.role for u in created} == set(Role)
    assert len(created) == len(DEV_USERS)
    assert seed_users(session) == []
    repo = UserRepository(session)
    assert len(repo.list()) == len(Role)
    for seed in DEV_USERS:
        assert repo.authenticate(seed.username, seed.password) is not None
        assert repo.authenticate(seed.username, "wrong") is None


def test_seed_default_config_only_when_missing(session: Session) -> None:
    assert seed_default_config(session) is True
    assert seed_default_config(session) is False
    repo = ConfigRepository(session)
    active = repo.get_active()
    assert active == SystemConfig()
    assert repo.list_versions()[0].version == 1
    assert repo.list_versions()[0].is_active


def test_seed_all_runs_both(session: Session) -> None:
    seed_all(session)
    assert ConfigRepository(session).has_active()
    assert UserRepository(session).get_by_username("admin") is not None


def test_seed_users_refuses_prod_unless_forced(session: Session) -> None:
    with pytest.raises(ConfigurationError, match="refusing to seed"):
        seed_users(session, environment="prod")
    assert UserRepository(session).list() == []
    with pytest.raises(ConfigurationError):
        seed_all(session, environment="prod")
    forced = seed_users(session, environment="prod", force=True)
    assert len(forced) == len(DEV_USERS)
    assert seed_users(session, environment="prod", force=True) == []  # still idempotent
