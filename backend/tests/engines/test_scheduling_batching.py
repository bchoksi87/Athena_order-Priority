"""Batching rules: pull forward allowed / denied by due date, gap, delay, locks."""

from __future__ import annotations

from app.domain.config import SchedulingConfig
from app.engines.calendar import MachineCalendar
from app.engines.constraints import MachineState
from app.engines.scheduling.batching import BatchCandidate, pick_next, shared_dimensions
from tests.engines.factories import NOW, at


def _state(**overrides: object) -> MachineState:
    fields: dict[str, object] = {
        "machine_id": "CNC-01",
        "next_free": NOW,
        "current_setup_family": "FAM-A",
        "current_material_id": "AL",
        "last_part_family": "PF-1",
    }
    fields.update(overrides)
    return MachineState(**fields)  # type: ignore[arg-type]


def _job(order_id: str, score: float, **overrides: object) -> BatchCandidate:
    fields: dict[str, object] = {
        "order_id": order_id,
        "operation_id": f"{order_id}-op1",
        "score": score,
        "duration_minutes": 60.0,
        "due_date": at(days=3),
    }
    fields.update(overrides)
    return BatchCandidate(**fields)  # type: ignore[arg-type]


class TestSharedDimensions:
    def test_setup_family_and_configured_dimensions(self, scheduling_config: SchedulingConfig) -> None:
        job = _job("O1", 50, setup_family="FAM-A", material_id="AL", part_family="PF-1", customer_id="C1")
        assert shared_dimensions(job, _state(), scheduling_config) == [
            "setup_family",
            "material",
            "part_family",
        ]

    def test_unconfigured_dimension_ignored(self) -> None:
        config = SchedulingConfig()
        config.batching.dimensions = ["customer"]
        job = _job("O1", 50, material_id="AL", customer_id="C1")
        assert shared_dimensions(job, _state(last_customer_id="C1"), config) == ["customer"]
        assert shared_dimensions(job, _state(last_customer_id="C2"), config) == []

    def test_tool_dimension_requires_all_mounted(self) -> None:
        config = SchedulingConfig()
        config.batching.dimensions = ["tool"]
        job = _job("O1", 50, tooling_ids=frozenset({"T1", "T2"}))
        assert shared_dimensions(job, _state(mounted_tooling={"T1", "T2", "T3"}), config) == ["tool"]
        assert shared_dimensions(job, _state(mounted_tooling={"T1"}), config) == []


class TestPickNext:
    def test_pull_forward_allowed(self, scheduling_config: SchedulingConfig) -> None:
        queue = [
            _job("HEAD", 80, material_id="ST"),
            _job("MID", 75, material_id="ST"),
            _job("BATCH", 70, material_id="AL"),
        ]
        decision = pick_next(queue, _state(), scheduling_config)
        assert decision is not None and decision.pulled_forward
        assert decision.chosen.order_id == "BATCH" and decision.bypassed == 2
        assert decision.matched_dimensions == ("material",)
        assert "delays HEAD by 1.0 h" in decision.reason and "score gap 10.0" in decision.reason

    def test_head_kept_when_it_already_shares_setup(self, scheduling_config: SchedulingConfig) -> None:
        queue = [_job("HEAD", 80, setup_family="FAM-A"), _job("B", 79, material_id="AL")]
        decision = pick_next(queue, _state(), scheduling_config)
        assert decision is not None and not decision.pulled_forward and decision.chosen.order_id == "HEAD"
        assert "already shares setup_family" in decision.reason

    def test_denied_by_priority_gap(self, scheduling_config: SchedulingConfig) -> None:
        gap = scheduling_config.batching.min_priority_gap
        queue = [_job("HEAD", 80, material_id="ST"), _job("BATCH", 80 - gap - 0.1, material_id="AL")]
        decision = pick_next(queue, _state(), scheduling_config)
        assert decision is not None and decision.chosen.order_id == "HEAD"
        queue[1] = _job("BATCH", 80 - gap, material_id="AL")  # exactly at the gap: allowed
        decision = pick_next(queue, _state(), scheduling_config)
        assert decision is not None and decision.chosen.order_id == "BATCH"

    def test_denied_by_max_delay(self, scheduling_config: SchedulingConfig) -> None:
        limit = scheduling_config.batching.max_delay_hours
        queue = [
            _job("HEAD", 80, material_id="ST"),
            _job("BATCH", 78, material_id="AL", duration_minutes=limit * 60 + 1),
        ]
        decision = pick_next(queue, _state(), scheduling_config)
        assert decision is not None and decision.chosen.order_id == "HEAD"

    def test_denied_when_head_due_date_would_be_missed(self, scheduling_config: SchedulingConfig) -> None:
        # head takes 60 min and is due in 90 min: a 60 min batch job in front makes it late
        queue = [
            _job("HEAD", 80, material_id="ST", due_date=at(minutes=90)),
            _job("BATCH", 78, material_id="AL"),
        ]
        decision = pick_next(queue, _state(), scheduling_config)
        assert decision is not None and decision.chosen.order_id == "HEAD"
        queue[0] = _job("HEAD", 80, material_id="ST", due_date=at(minutes=120))  # exactly fits
        decision = pick_next(queue, _state(), scheduling_config)
        assert decision is not None and decision.chosen.order_id == "BATCH"

    def test_due_date_check_uses_calendar(
        self, scheduling_config: SchedulingConfig, day_calendar: MachineCalendar
    ) -> None:
        # machine free at 15:00: head (60 min) ends 16:00 today; a 60 min batch job in front
        # pushes it to tomorrow 09:00
        state = _state(next_free=at(hours=7))
        queue = [
            _job("HEAD", 80, material_id="ST", due_date=at(hours=8)),
            _job("BATCH", 78, material_id="AL"),
        ]
        decision = pick_next(queue, state, scheduling_config, calendar=day_calendar)
        assert decision is not None and decision.chosen.order_id == "HEAD"
        queue[0] = _job("HEAD", 80, material_id="ST", due_date=at(days=1, hours=1))
        decision = pick_next(queue, state, scheduling_config, calendar=day_calendar)
        assert decision is not None and decision.chosen.order_id == "BATCH"

    def test_bypassed_middle_job_due_date_protected(self, scheduling_config: SchedulingConfig) -> None:
        queue = [
            _job("HEAD", 80, material_id="ST"),
            _job(
                "MID", 79, material_id="ST", due_date=at(minutes=150)
            ),  # 60+60 = 120 ok, +60 batch = 180 late
            _job("BATCH", 78, material_id="AL"),
        ]
        decision = pick_next(queue, _state(), scheduling_config)
        assert decision is not None and decision.chosen.order_id == "HEAD"

    def test_never_bypasses_forced_or_locked(self, scheduling_config: SchedulingConfig) -> None:
        queue = [
            _job("HEAD", 80, material_id="ST"),
            _job("LOCKED", 79, material_id="ST", locked=True),
            _job("BATCH", 78, material_id="AL"),
        ]
        decision = pick_next(queue, _state(), scheduling_config)
        assert decision is not None and decision.chosen.order_id == "HEAD"
        forced_head = [
            _job("HEAD", 80, material_id="ST", forced_next=True),
            _job("BATCH", 78, material_id="AL"),
        ]
        decision = pick_next(forced_head, _state(), scheduling_config)
        assert decision is not None and decision.chosen.order_id == "HEAD" and "forced" in decision.reason
        forced_cand = [
            _job("HEAD", 80, material_id="ST"),
            _job("BATCH", 78, material_id="AL", forced_next=True),
        ]
        decision = pick_next(forced_cand, _state(), scheduling_config)
        assert decision is not None and decision.chosen.order_id == "HEAD"

    def test_disabled_and_empty(self) -> None:
        config = SchedulingConfig()
        config.batching.enabled = False
        queue = [_job("HEAD", 80, material_id="ST"), _job("BATCH", 78, material_id="AL")]
        decision = pick_next(queue, _state(), config)
        assert decision is not None and decision.chosen.order_id == "HEAD" and "disabled" in decision.reason
        assert pick_next([], _state(), config) is None
