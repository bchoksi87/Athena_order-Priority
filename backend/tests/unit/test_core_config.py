"""Settings parsing and defaults."""

from __future__ import annotations

import warnings

import pytest

from app.core.config import (
    API_PREFIX,
    DEFAULT_DATABASE_URL,
    DEFAULT_DEV_JWT_SECRET,
    Settings,
    get_settings,
    reset_settings_cache,
)
from app.domain.enums import WritebackMode

pytestmark = pytest.mark.unit


def _settings(**kwargs: object) -> Settings:
    return Settings(_env_file=None, **kwargs)  # type: ignore[call-arg]


def test_defaults_are_dev_friendly(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(dict(**__import__("os").environ)):
        if key.startswith("PPSE_"):
            monkeypatch.delenv(key)
    s = _settings()
    assert s.environment == "dev"
    assert s.database_url == DEFAULT_DATABASE_URL
    assert s.jwt_secret == DEFAULT_DEV_JWT_SECRET
    assert s.jwt_algorithm == "HS256"
    assert s.api_prefix == API_PREFIX
    assert s.writeback_mode is WritebackMode.READ_ONLY
    assert s.erp_connector == "mock"
    assert s.synthetic_scale == "small"
    assert s.currency == "INR"
    assert s.is_dev and not s.is_prod and s.uses_default_secret


def test_env_prefix_and_types(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PPSE_ENVIRONMENT", "prod")
    monkeypatch.setenv("PPSE_JWT_SECRET", "s3cret")
    monkeypatch.setenv("PPSE_JWT_EXPIRE_MINUTES", "15")
    monkeypatch.setenv("PPSE_LOG_JSON", "true")
    monkeypatch.setenv("PPSE_LOG_LEVEL", "debug")
    monkeypatch.setenv("PPSE_WRITEBACK_MODE", "approval")
    monkeypatch.setenv("PPSE_SYNTHETIC_SCALE", "large")
    monkeypatch.setenv("PPSE_BACKGROUND_JOBS_ENABLED", "1")
    s = _settings()
    assert s.environment == "prod" and s.is_prod
    assert s.jwt_secret == "s3cret" and not s.uses_default_secret
    assert s.jwt_expire_minutes == 15
    assert s.log_json is True
    assert s.log_level == "DEBUG"
    assert s.writeback_mode is WritebackMode.APPROVAL
    assert s.synthetic_scale == "large"
    assert s.background_jobs_enabled is True


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://a.example, http://b.example", ["http://a.example", "http://b.example"]),
        ('["http://x", "http://y"]', ["http://x", "http://y"]),
        ("", []),
    ],
)
def test_cors_origins_accept_csv_and_json(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: list[str]
) -> None:
    monkeypatch.setenv("PPSE_CORS_ORIGINS", raw)
    assert _settings().cors_origins == expected


def test_cors_origins_from_python_list() -> None:
    assert _settings(cors_origins=["http://z"]).cors_origins == ["http://z"]


def test_prod_with_default_secret_warns() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        s = _settings(environment="prod")
    assert s.uses_default_secret
    assert any("PPSE_JWT_SECRET" in str(w.message) for w in caught)


def test_dev_with_default_secret_does_not_warn() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _settings(environment="dev")
    assert not [w for w in caught if "PPSE_JWT_SECRET" in str(w.message)]


def test_api_prefix_validation() -> None:
    assert _settings(api_prefix="/v2/").api_prefix == "/v2"
    with pytest.raises(ValueError):
        _settings(api_prefix="v2")


def test_invalid_enum_values_rejected() -> None:
    with pytest.raises(ValueError):
        _settings(environment="staging")
    with pytest.raises(ValueError):
        _settings(synthetic_scale="huge")
    with pytest.raises(ValueError):
        _settings(jwt_expire_minutes=0)


def test_effective_database_url_prefers_test_db_in_test_env() -> None:
    s = _settings(environment="test", database_url="sqlite://", test_database_url="sqlite:///t.db")
    assert s.effective_database_url == "sqlite:///t.db"
    s = _settings(environment="dev", database_url="sqlite://", test_database_url="sqlite:///t.db")
    assert s.effective_database_url == "sqlite://"


def test_get_settings_is_cached_and_resettable(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_settings_cache()
    monkeypatch.setenv("PPSE_CURRENCY", "EUR")
    first = get_settings()
    assert first.currency == "EUR"
    monkeypatch.setenv("PPSE_CURRENCY", "USD")
    assert get_settings() is first
    reset_settings_cache()
    assert get_settings().currency == "USD"
    reset_settings_cache()
