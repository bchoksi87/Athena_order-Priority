"""Tests for analytics.alerts: every rule fires and does not fire; dedupe keys are stable."""

from __future__ import annotations

from collections.abc import Mapping

from app.domain.config import AlertConfig, PriorityProfile
from app.domain.enums import (
    AlertSeverity,
    AlertType,
    DataQualityCode,
    DataQualitySeverity,
    MachineStatus,
    OperationStatus,
    ReadinessState,
    RiskLevel,
)
from app.domain.results import Alert, Bottleneck, DataQualityIssue, FactorScore, ReplanDecision
from app.engines.analytics.alerts import ALERT_RULES, alert_sort_key, dedupe_alerts, evaluate_alerts
from app.engines.analytics.capacity import compute_capacity
from app.engines.calendar import build_calendars
from tests.engines.factories import (
    NOW,
    at,
    make_calendar_spec,
    make_entry,
    make_machine,
    make_material,
    make_order_with_ops,
    make_priority,
    make_schedule,
    make_snapshot,
    make_tooling,
    window,
)

ALERTS = AlertConfig()  # likely late 8 h, overload 95 %, material 3 days, starvation 10 d, behind 60 min
CAL = make_calendar_spec("CAL")


def _plant(orders_with_ops=(), machines=None, **extra):  # type: ignore[no-untyped-def]
    orders = [o for o, _ in orders_with_ops]
    ops = [op for _, ops in orders_with_ops for op in ops]
    return make_snapshot(
        orders=orders,
        operations=ops,
        machines=machines if machines is not None else [make_machine("CNC-01", calendar_id="CAL")],
        calendars=[CAL],
        default_calendar_id="CAL",
        **extra,
    )


def _only(alert_type: AlertType) -> Mapping[AlertType, object]:
    return {alert_type: ALERT_RULES[alert_type]}


def _run(snapshot, priorities=None, schedule=None, alert_type=None, **kwargs) -> list[Alert]:  # type: ignore[no-untyped-def]
    rules = _only(alert_type) if alert_type is not None else None
    return evaluate_alerts(
        snapshot, priorities or {}, schedule, None, None, NOW, ALERTS, rules=rules, **kwargs
    )  # type: ignore[arg-type]


def test_every_alert_type_has_a_rule() -> None:
    assert set(ALERT_RULES) == set(AlertType)


def test_likely_late_fires_high_on_projection_warning_on_slack_and_not_otherwise() -> None:
    plant = _plant(
        [
            make_order_with_ops("LATE", requested_delivery_date=at(days=1)),
            make_order_with_ops("TIGHT", requested_delivery_date=at(hours=5)),
            make_order_with_ops("FINE", requested_delivery_date=at(days=5)),
            make_order_with_ops("OVERDUE", requested_delivery_date=at(hours=-1)),
        ]
    )
    priorities = {
        "LATE": make_priority("LATE", projected_completion=at(days=1, hours=3), projected_lateness_hours=3.0)
    }
    alerts = _run(plant, priorities, alert_type=AlertType.ORDER_LIKELY_LATE)
    by_order = {a.order_id: a for a in alerts}
    assert set(by_order) == {"LATE", "TIGHT"}
    assert (
        by_order["LATE"].severity is AlertSeverity.HIGH
        and "3.0 h after the due date" in by_order["LATE"].reason
    )
    assert (
        by_order["TIGHT"].severity is AlertSeverity.WARNING and "5.0 h of slack" in by_order["TIGHT"].reason
    )
    assert by_order["LATE"].dedupe_key == "order_likely_late:LATE:2026-09-07"
    assert "Expedite order LATE" in by_order["LATE"].recommended_action


def test_overdue_fires_critical_only_for_past_due() -> None:
    plant = _plant(
        [make_order_with_ops("OD", requested_delivery_date=at(hours=-30)), make_order_with_ops("OK")]
    )
    schedule = make_schedule([make_entry("OD", start=NOW, run_minutes=60, operation_id="OD-op1")])
    alerts = _run(plant, {}, schedule, alert_type=AlertType.ORDER_OVERDUE)
    assert [a.order_id for a in alerts] == ["OD"]
    alert = alerts[0]
    assert alert.severity is AlertSeverity.CRITICAL and alert.alert_type is AlertType.ORDER_OVERDUE
    assert "30.0 h ago" in alert.reason and "projected completion" in alert.reason
    assert alert.recommended_action.startswith(
        "Inform Customer C1 of a revised delivery date; Expedite or re-sequence order OD"
    )


def test_machine_downtime_rules() -> None:
    plant = _plant(
        [make_order_with_ops("O1", op_overrides=[{"machine_id": "DOWN"}])],
        machines=[
            make_machine("DOWN", calendar_id="CAL", status=MachineStatus.DOWN, available_from=at(days=1)),
            make_machine("MAINT", calendar_id="CAL", status=MachineStatus.MAINTENANCE),
            make_machine(
                "ACTIVE", calendar_id="CAL", planned_downtime=[window(at(hours=-1), 3, "filter change")]
            ),
            make_machine("SOON", calendar_id="CAL", maintenance_windows=[window(at(hours=10), 2, "PM")]),
            make_machine("LATER", calendar_id="CAL", maintenance_windows=[window(at(hours=30), 2)]),
            make_machine("PAST", calendar_id="CAL", unplanned_downtime=[window(at(hours=-5), 2)]),
            make_machine("FINE", calendar_id="CAL"),
        ],
    )
    schedule = make_schedule([make_entry("O1", "DOWN", start=at(days=1), run_minutes=60)])
    alerts = _run(plant, {}, schedule, alert_type=AlertType.MACHINE_DOWNTIME)
    by_machine = {a.machine_id: a for a in alerts}
    assert set(by_machine) == {"DOWN", "MAINT", "ACTIVE", "SOON"}
    assert by_machine["DOWN"].severity is AlertSeverity.CRITICAL
    assert "expected back 2026-09-08 08:00 UTC" in by_machine["DOWN"].reason
    assert "1 scheduled job(s) and 1 waiting order(s)" in by_machine["DOWN"].reason
    assert by_machine["DOWN"].recommended_action.startswith("Re-route affected work to ACTIVE, FINE, LATER")
    assert by_machine["MAINT"].severity is AlertSeverity.WARNING
    assert (
        by_machine["ACTIVE"].severity is AlertSeverity.HIGH and "filter change" in by_machine["ACTIVE"].reason
    )
    assert by_machine["SOON"].severity is AlertSeverity.WARNING and "within 24 h" in by_machine["SOON"].reason
    assert by_machine["DOWN"].dedupe_key == "machine_downtime:DOWN:2026-09-07"


def test_material_shortage_rules() -> None:
    plant = _plant(
        [
            make_order_with_ops(
                "TODAY", requested_delivery_date=at(hours=2), op_overrides=[{"material_id": "MAT1"}]
            ),
            make_order_with_ops(
                "SOON", requested_delivery_date=at(days=2), op_overrides=[{"material_id": "MAT2"}]
            ),
            make_order_with_ops(
                "FAR", requested_delivery_date=at(days=10), op_overrides=[{"material_id": "MAT3"}]
            ),
            make_order_with_ops("NOMAT", requested_delivery_date=at(days=1)),
            make_order_with_ops(
                "READY", requested_delivery_date=at(days=1), op_overrides=[{"material_id": "MAT1"}]
            ),
        ],
        materials=[make_material("MAT1", 0.0, incoming_quantity=3.0, expected_receipt_date=at(days=1))],
    )
    priorities = {
        oid: make_priority(oid, readiness=ReadinessState.WAITING_MATERIAL, blocked=True)
        for oid in ("TODAY", "SOON", "FAR", "NOMAT")
    }
    priorities["READY"] = make_priority("READY")
    alerts = _run(plant, priorities, alert_type=AlertType.MATERIAL_SHORTAGE)
    by_entity = {a.entity_ref: a for a in alerts}
    assert set(by_entity) == {"MAT1", "MAT2", "unknown"}
    assert (
        by_entity["MAT1"].severity is AlertSeverity.CRITICAL
        and "0 kg free, 3 incoming" in by_entity["MAT1"].reason
    )
    assert by_entity["MAT1"].details["blocked_orders"] == ["TODAY"]
    assert by_entity["MAT2"].severity is AlertSeverity.HIGH
    assert by_entity["unknown"].details["blocked_orders"] == ["NOMAT"]
    assert by_entity["MAT1"].recommended_action.startswith("Expedite the supplier delivery of MAT1")


def test_tool_shortage_rules() -> None:
    plant = _plant(
        [
            make_order_with_ops(
                "A", requested_delivery_date=at(days=1), op_overrides=[{"tooling_ids": {"T1", "T2"}}]
            ),
            make_order_with_ops("B", requested_delivery_date=at(days=1), tooling_requirement={"T2"}),
            make_order_with_ops("C", requested_delivery_date=at(days=1)),
        ],
        tooling=[make_tooling("T1", available=False), make_tooling("T2")],
    )
    priorities = {
        oid: make_priority(oid, readiness=ReadinessState.WAITING_TOOLING, blocked=True) for oid in "ABC"
    }
    alerts = _run(plant, priorities, alert_type=AlertType.TOOL_SHORTAGE)
    by_entity = {a.entity_ref: a for a in alerts}
    assert set(by_entity) == {"T1", "T2", "unknown"}
    assert by_entity["T1"].details["blocked_orders"] == ["A"]  # only the unusable tool of A
    assert by_entity["T2"].details["blocked_orders"] == ["B"]  # all required tools when none is unusable
    assert (
        by_entity["T1"].alert_type is AlertType.TOOL_SHORTAGE
        and by_entity["T1"].severity is AlertSeverity.HIGH
    )


def test_capacity_overload_and_bottleneck_rules() -> None:
    order, ops = make_order_with_ops(
        "BIG", op_overrides=[{"cycle_minutes_per_unit": 300.0}]
    )  # 50.5 h vs 40 h
    plant = _plant([(order, ops)])
    capacity = compute_capacity(plant, None, build_calendars(plant), NOW, 7, "machine_group", "week")
    alerts = _run(plant, {}, alert_type=AlertType.CAPACITY_OVERLOAD, capacity=capacity)
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.severity is AlertSeverity.HIGH and alert.entity_ref == "CNC@2026-09-07"
    assert "126% >= 95%" in alert.reason and alert.recommended_action.startswith(
        "Add 10 h of capacity on CNC"
    )
    assert _run(plant, {}, alert_type=AlertType.CAPACITY_OVERLOAD) == []  # no capacity report -> nothing
    light, light_ops = make_order_with_ops("SMALL")
    quiet = _plant([(light, light_ops)])
    quiet_capacity = compute_capacity(quiet, None, build_calendars(quiet), NOW, 7, "machine_group", "week")
    assert _run(quiet, {}, alert_type=AlertType.CAPACITY_OVERLOAD, capacity=quiet_capacity) == []

    bottleneck = Bottleneck(
        "machine_group", "CNC", "CNC", 126.0, 1, 10.5, 0.0, 0.0, RiskLevel.CRITICAL, "Add 11 machine hours"
    )
    alerts = evaluate_alerts(
        plant, {}, None, [bottleneck], None, NOW, ALERTS, rules=_only(AlertType.BOTTLENECK)
    )  # type: ignore[arg-type]
    assert len(alerts) == 1 and alerts[0].severity is AlertSeverity.CRITICAL
    assert (
        alerts[0].recommended_action == "Add 11 machine hours" and alerts[0].entity_ref == "machine_group:CNC"
    )
    assert evaluate_alerts(plant, {}, None, [], None, NOW, ALERTS, rules=_only(AlertType.BOTTLENECK)) == []  # type: ignore[arg-type]


def _sla_factor(raw: float, remaining: float) -> FactorScore:
    return FactorScore(
        "sla_risk",
        "SLA Risk",
        "bonus",
        raw,
        0.15,
        raw * 0.15,
        f"SLA 48 h: {remaining:g} h remaining",
        {"remaining_hours": remaining, "sla_hours": 48.0},
    )


def test_sla_breach_rule_uses_profile_thresholds() -> None:
    plant = _plant(
        [
            make_order_with_ops("IMM"),
            make_order_with_ops("BR"),
            make_order_with_ops("OK"),
            make_order_with_ops("NOSLA"),
        ]
    )
    priorities = {
        "IMM": make_priority("IMM", factors=[_sla_factor(85.0, 6.0)]),
        "BR": make_priority("BR", factors=[_sla_factor(100.0, -2.0)]),
        "OK": make_priority("OK", factors=[_sla_factor(55.0, 20.0)]),
        "NOSLA": make_priority("NOSLA"),
    }
    alerts = _run(plant, priorities, alert_type=AlertType.SLA_BREACH_RISK)
    by_order = {a.order_id: a for a in alerts}
    assert set(by_order) == {"IMM", "BR"}
    assert by_order["IMM"].severity is AlertSeverity.HIGH and "breach imminent" in by_order["IMM"].title
    assert by_order["BR"].severity is AlertSeverity.CRITICAL and "SLA breached" in by_order["BR"].title
    assert "48 h SLA" in by_order["IMM"].recommended_action
    strict = PriorityProfile.model_validate({"sla": {"imminent_score": 50.0}})
    assert len(_run(plant, priorities, alert_type=AlertType.SLA_BREACH_RISK, profile=strict)) == 3


def test_production_behind_schedule_rules() -> None:
    late_start, late_ops = make_order_with_ops(
        "LATE", op_overrides=[{"estimated_start": at(hours=-3), "actual_start": at(hours=-1)}]
    )
    ok_start, ok_ops = make_order_with_ops(
        "OK", op_overrides=[{"estimated_start": at(hours=-2), "actual_start": at(hours=-1, minutes=-30)}]
    )
    missed, missed_ops = make_order_with_ops("MISSED")
    running, running_ops = make_order_with_ops(
        "RUN", op_overrides=[{"operation_status": OperationStatus.IN_PROGRESS}]
    )
    plant = _plant([(late_start, late_ops), (ok_start, ok_ops), (missed, missed_ops), (running, running_ops)])
    schedule = make_schedule(
        [
            make_entry("MISSED", start=at(hours=-2), run_minutes=60, operation_id="MISSED-op1"),
            make_entry(
                "RUN", start=at(hours=-2), run_minutes=60, operation_id="RUN-op1", sequence_on_machine=2
            ),
        ]
    )
    alerts = _run(plant, {}, schedule, alert_type=AlertType.PRODUCTION_BEHIND_SCHEDULE)
    by_order = {a.order_id: a for a in alerts}
    assert set(by_order) == {"LATE", "MISSED"}
    assert "started 2.0 h after its planned start" in by_order["LATE"].reason
    assert by_order["LATE"].details["delay_minutes"] == 120.0
    assert (
        "has not started (2.0 h ago)" in by_order["MISSED"].reason
        and by_order["MISSED"].machine_id == "CNC-01"
    )
    assert by_order["MISSED"].severity is AlertSeverity.WARNING


def test_schedule_disruption_rule() -> None:
    plant = _plant()

    def decision(**kw):  # type: ignore[no-untyped-def]
        base = {
            "should_replan": True,
            "reason": "machine down",
            "improvement_pct": 4.0,
            "changed_entries": 5,
            "frozen_violations": 0,
            "requires_approval": True,
            "triggers": ["machine_down"],
        }
        base.update(kw)
        return ReplanDecision(**base)

    plain = _run(plant, alert_type=AlertType.SCHEDULE_DISRUPTION, disruption=decision())
    assert len(plain) == 1 and plain[0].severity is AlertSeverity.WARNING
    assert "5 schedule entries change (+4.0% quality); triggers: machine_down" in plain[0].reason
    assert plain[0].recommended_action.startswith("Review and approve")
    assert plain[0].dedupe_key == "schedule_disruption:replan:2026-09-07"
    assert (
        _run(plant, alert_type=AlertType.SCHEDULE_DISRUPTION, disruption=decision(frozen_violations=2))[
            0
        ].severity
        is AlertSeverity.HIGH
    )
    assert (
        _run(plant, alert_type=AlertType.SCHEDULE_DISRUPTION, disruption=decision(should_replan=False))[
            0
        ].severity
        is AlertSeverity.INFO
    )
    assert _run(plant, alert_type=AlertType.SCHEDULE_DISRUPTION, disruption=decision(changed_entries=0)) == []
    assert _run(plant, alert_type=AlertType.SCHEDULE_DISRUPTION) == []


def test_starvation_rule() -> None:
    plant = _plant(
        [
            make_order_with_ops("OLD", received_date=at(days=-12)),
            make_order_with_ops(
                "STARTED",
                received_date=at(days=-12),
                op_overrides=[{"operation_status": OperationStatus.IN_PROGRESS}],
            ),
            make_order_with_ops("YOUNG", order_date=at(days=-5)),
            make_order_with_ops("NODATE"),
        ]
    )
    priorities = {"OLD": make_priority("OLD", score=33.0, risk_level=RiskLevel.CRITICAL)}
    alerts = _run(plant, priorities, alert_type=AlertType.STARVATION)
    assert [a.order_id for a in alerts] == ["OLD"]
    assert alerts[0].severity is AlertSeverity.CRITICAL and "waiting 12.0 days" in alerts[0].reason
    assert "priority score 33" in alerts[0].reason and alerts[0].details["waiting_days"] == 12.0
    assert _run(plant, {}, alert_type=AlertType.STARVATION)[0].severity is AlertSeverity.HIGH


def test_data_quality_rule_groups_blocking_issues_by_code() -> None:
    plant = _plant()
    issues = [
        DataQualityIssue(
            DataQualityCode.MISSING_DUE_DATE,
            DataQualitySeverity.BLOCKING,
            "order",
            "O1",
            "no due",
            recommendation="Set a due date",
        ),
        DataQualityIssue(
            DataQualityCode.MISSING_DUE_DATE, DataQualitySeverity.BLOCKING, "order", "O2", "no due"
        ),
        DataQualityIssue(
            DataQualityCode.MISSING_CYCLE_TIME,
            DataQualitySeverity.BLOCKING,
            "operation",
            "O3-op1",
            "no cycle",
            details={"order_id": "O3"},
        ),
        DataQualityIssue(
            DataQualityCode.MISSING_CYCLE_TIME,
            DataQualitySeverity.BLOCKING,
            "operation",
            "O3-op2",
            "no cycle",
            details={"order_id": "O3"},
        ),
        DataQualityIssue(DataQualityCode.DUPLICATE_ORDER, DataQualitySeverity.WARNING, "order", "O4", "dup"),
    ]
    alerts = evaluate_alerts(plant, {}, None, None, issues, NOW, ALERTS, rules=_only(AlertType.DATA_QUALITY))  # type: ignore[arg-type]
    by_entity = {a.entity_ref: a for a in alerts}
    assert set(by_entity) == {"missing_due_date", "missing_cycle_time"}
    due = by_entity["missing_due_date"]
    assert (
        due.title == "2 order(s) cannot be scheduled: missing due date"
        and due.recommended_action == "Set a due date"
    )
    assert by_entity["missing_cycle_time"].details["entities"] == ["O3"]
    assert all(a.severity is AlertSeverity.HIGH for a in alerts)
    assert (
        evaluate_alerts(plant, {}, None, None, issues[-1:], NOW, ALERTS, rules=_only(AlertType.DATA_QUALITY))
        == []
    )  # type: ignore[arg-type]


def test_full_evaluation_is_sorted_deduplicated_and_stable() -> None:
    plant = _plant(
        [
            make_order_with_ops("OD", requested_delivery_date=at(hours=-1), received_date=at(days=-15)),
            make_order_with_ops("TIGHT", requested_delivery_date=at(hours=3)),
        ],
        machines=[make_machine("CNC-01", calendar_id="CAL", status=MachineStatus.DOWN)],
    )
    first = evaluate_alerts(plant, {}, None, None, None, NOW, ALERTS)
    second = evaluate_alerts(plant, {}, None, None, None, NOW, ALERTS)
    assert [a.dedupe_key for a in first] == [a.dedupe_key for a in second]
    assert first == sorted(first, key=alert_sort_key)
    assert [a.alert_type for a in first] == [
        AlertType.ORDER_OVERDUE,
        AlertType.MACHINE_DOWNTIME,
        AlertType.STARVATION,
        AlertType.ORDER_LIKELY_LATE,
    ]
    assert len({a.dedupe_key for a in first}) == len(first)
    assert all(a.raised_at == NOW for a in first)
    duplicated = dedupe_alerts(first + first)
    assert duplicated == first
