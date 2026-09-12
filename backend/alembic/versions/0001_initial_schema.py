"""Initial schema: every table of docs/DESIGN_CONTRACT.md §7 (plus system_configs).

Portable types only (String/Integer/Float/Boolean/DateTime(timezone=True)/JSON/Text/LargeBinary);
enum columns are String. Hand-reviewed after autogenerate against PostgreSQL 16.

Revision ID: 0001
Revises:
Create Date: 2026-09-11 12:27:18.334848+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "alerts",
        sa.Column("alert_id", sa.String(length=64), nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("alert_type", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("recommended_action", sa.Text(), nullable=False),
        sa.Column("raised_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("occurrences", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.String(length=64), nullable=True),
        sa.Column("machine_id", sa.String(length=64), nullable=True),
        sa.Column("entity_ref", sa.String(length=128), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("acknowledged_by", sa.String(length=64), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("alert_id"),
        sa.UniqueConstraint("dedupe_key"),
    )
    op.create_index(op.f("ix_alerts_active"), "alerts", ["active"], unique=False)
    op.create_index("ix_alerts_active_raised", "alerts", ["active", "raised_at"], unique=False)
    op.create_index("ix_alerts_active_severity", "alerts", ["active", "severity"], unique=False)
    op.create_index(op.f("ix_alerts_alert_type"), "alerts", ["alert_type"], unique=False)
    op.create_index(op.f("ix_alerts_machine_id"), "alerts", ["machine_id"], unique=False)
    op.create_index(op.f("ix_alerts_order_id"), "alerts", ["order_id"], unique=False)
    op.create_index(op.f("ix_alerts_raised_at"), "alerts", ["raised_at"], unique=False)
    op.create_index(op.f("ix_alerts_severity"), "alerts", ["severity"], unique=False)
    op.create_table(
        "audit_log",
        sa.Column("audit_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("previous_value", sa.JSON(), nullable=True),
        sa.Column("new_value", sa.JSON(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("audit_id"),
    )
    op.create_index(op.f("ix_audit_log_action"), "audit_log", ["action"], unique=False)
    op.create_index("ix_audit_log_entity", "audit_log", ["entity_type", "entity_id"], unique=False)
    op.create_index(op.f("ix_audit_log_entity_type"), "audit_log", ["entity_type"], unique=False)
    op.create_index(op.f("ix_audit_log_timestamp"), "audit_log", ["timestamp"], unique=False)
    op.create_index(op.f("ix_audit_log_user_id"), "audit_log", ["user_id"], unique=False)
    op.create_index("ix_audit_log_user_time", "audit_log", ["user_id", "timestamp"], unique=False)
    op.create_table(
        "calendar_specs",
        sa.Column("calendar_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("shifts", sa.JSON(), nullable=False),
        sa.Column("holidays", sa.JSON(), nullable=False),
        sa.Column("overtime_windows", sa.JSON(), nullable=False),
        sa.Column("extra_working_days", sa.JSON(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("calendar_id"),
    )
    op.create_index(op.f("ix_calendar_specs_is_default"), "calendar_specs", ["is_default"], unique=False)
    op.create_table(
        "customers",
        sa.Column("customer_id", sa.String(length=64), nullable=False),
        sa.Column("customer_name", sa.String(length=255), nullable=False),
        sa.Column("customer_category", sa.String(length=32), nullable=False),
        sa.Column("customer_tier", sa.String(length=32), nullable=False),
        sa.Column("customer_priority", sa.Integer(), nullable=False),
        sa.Column("strategic_customer_flag", sa.Boolean(), nullable=False),
        sa.Column("annual_revenue", sa.Float(), nullable=True),
        sa.Column("customer_revenue", sa.Float(), nullable=True),
        sa.Column("customer_profitability", sa.Float(), nullable=True),
        sa.Column("customer_service_level", sa.Float(), nullable=True),
        sa.Column("sla_hours", sa.Float(), nullable=True),
        sa.Column("escalation_level", sa.Integer(), nullable=False),
        sa.Column("historical_on_time_delivery", sa.Float(), nullable=True),
        sa.Column("payment_risk", sa.String(length=32), nullable=False),
        sa.Column("preferred_delivery_expectation", sa.String(length=255), nullable=True),
        sa.Column("account_manager", sa.String(length=255), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("external_ref", sa.String(length=128), nullable=True),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("customer_id"),
    )
    op.create_index(op.f("ix_customers_active"), "customers", ["active"], unique=False)
    op.create_index(op.f("ix_customers_customer_tier"), "customers", ["customer_tier"], unique=False)
    op.create_table(
        "data_quality_issues",
        sa.Column("issue_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("field_name", sa.String(length=128), nullable=True),
        sa.Column("recommendation", sa.Text(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("issue_id"),
    )
    op.create_index(op.f("ix_data_quality_issues_code"), "data_quality_issues", ["code"], unique=False)
    op.create_index(
        op.f("ix_data_quality_issues_entity_id"), "data_quality_issues", ["entity_id"], unique=False
    )
    op.create_index(
        "ix_data_quality_issues_run_code", "data_quality_issues", ["run_id", "code"], unique=False
    )
    op.create_index(
        "ix_data_quality_issues_run_entity",
        "data_quality_issues",
        ["run_id", "entity_type", "entity_id"],
        unique=False,
    )
    op.create_index(op.f("ix_data_quality_issues_run_id"), "data_quality_issues", ["run_id"], unique=False)
    op.create_index(
        op.f("ix_data_quality_issues_severity"), "data_quality_issues", ["severity"], unique=False
    )
    op.create_table(
        "expedites",
        sa.Column("expedite_id", sa.String(length=64), nullable=False),
        sa.Column("order_id", sa.String(length=64), nullable=False),
        sa.Column("boost_points", sa.Float(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("expedite_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("released_by", sa.String(length=64), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("expedite_id"),
    )
    op.create_index(op.f("ix_expedites_active"), "expedites", ["active"], unique=False)
    op.create_index(op.f("ix_expedites_expires_at"), "expedites", ["expires_at"], unique=False)
    op.create_index("ix_expedites_order_active", "expedites", ["order_id", "active"], unique=False)
    op.create_index(op.f("ix_expedites_order_id"), "expedites", ["order_id"], unique=False)
    op.create_table(
        "input_snapshots",
        sa.Column("snapshot_id", sa.String(length=64), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("codec", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("snapshot_id"),
    )
    op.create_index(op.f("ix_input_snapshots_as_of"), "input_snapshots", ["as_of"], unique=False)
    op.create_table(
        "machines",
        sa.Column("machine_id", sa.String(length=64), nullable=False),
        sa.Column("machine_name", sa.String(length=255), nullable=False),
        sa.Column("machine_type", sa.String(length=128), nullable=False),
        sa.Column("process_type", sa.String(length=32), nullable=False),
        sa.Column("machine_group", sa.String(length=128), nullable=False),
        sa.Column("location", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("calendar_id", sa.String(length=64), nullable=True),
        sa.Column("efficiency", sa.Float(), nullable=False),
        sa.Column("utilization", sa.Float(), nullable=True),
        sa.Column("capacity_hours_per_day", sa.Float(), nullable=True),
        sa.Column("compatible_materials", sa.JSON(), nullable=False),
        sa.Column("compatible_processes", sa.JSON(), nullable=False),
        sa.Column("max_part_size_mm", sa.JSON(), nullable=True),
        sa.Column("tooling_configuration", sa.JSON(), nullable=False),
        sa.Column("setup_requirements", sa.JSON(), nullable=False),
        sa.Column("current_material_id", sa.String(length=64), nullable=True),
        sa.Column("current_setup_family", sa.String(length=128), nullable=True),
        sa.Column("available_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("preferred_rank", sa.Integer(), nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("machine_id"),
    )
    op.create_index("ix_machines_group_rank", "machines", ["machine_group", "preferred_rank"], unique=False)
    op.create_index(op.f("ix_machines_machine_group"), "machines", ["machine_group"], unique=False)
    op.create_index(op.f("ix_machines_process_type"), "machines", ["process_type"], unique=False)
    op.create_index(op.f("ix_machines_status"), "machines", ["status"], unique=False)
    op.create_table(
        "materials",
        sa.Column("material_id", sa.String(length=64), nullable=False),
        sa.Column("material_name", sa.String(length=255), nullable=False),
        sa.Column("material_type", sa.String(length=128), nullable=False),
        sa.Column("grade", sa.String(length=128), nullable=True),
        sa.Column("supplier", sa.String(length=255), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("available_quantity", sa.Float(), nullable=False),
        sa.Column("reserved_quantity", sa.Float(), nullable=False),
        sa.Column("incoming_quantity", sa.Float(), nullable=False),
        sa.Column("expected_receipt_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("minimum_stock", sa.Float(), nullable=False),
        sa.Column("compatible_machine_ids", sa.JSON(), nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("material_id"),
    )
    op.create_table(
        "orders",
        sa.Column("order_id", sa.String(length=64), nullable=False),
        sa.Column("customer_id", sa.String(length=64), nullable=False),
        sa.Column("part_id", sa.String(length=64), nullable=False),
        sa.Column("order_line_id", sa.String(length=64), nullable=True),
        sa.Column("external_order_ref", sa.String(length=128), nullable=True),
        sa.Column("part_name", sa.String(length=255), nullable=True),
        sa.Column("part_family", sa.String(length=128), nullable=True),
        sa.Column("order_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_delivery_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("promised_delivery_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revised_delivery_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("completed_quantity", sa.Float(), nullable=False),
        sa.Column("cancelled_quantity", sa.Float(), nullable=False),
        sa.Column("order_status", sa.String(length=32), nullable=False),
        sa.Column("erp_priority", sa.Integer(), nullable=True),
        sa.Column("production_status", sa.String(length=128), nullable=True),
        sa.Column("material_status", sa.String(length=32), nullable=False),
        sa.Column("quality_status", sa.String(length=32), nullable=False),
        sa.Column("shipping_status", sa.String(length=32), nullable=False),
        sa.Column("order_value", sa.Float(), nullable=True),
        sa.Column("estimated_cost", sa.Float(), nullable=True),
        sa.Column("estimated_margin", sa.Float(), nullable=True),
        sa.Column("actual_margin", sa.Float(), nullable=True),
        sa.Column("process_type", sa.String(length=32), nullable=False),
        sa.Column("manufacturing_route", sa.JSON(), nullable=False),
        sa.Column("machine_group", sa.String(length=128), nullable=True),
        sa.Column("required_machine_id", sa.String(length=64), nullable=True),
        sa.Column("required_material_id", sa.String(length=64), nullable=True),
        sa.Column("tooling_requirement", sa.JSON(), nullable=False),
        sa.Column("estimated_setup_minutes", sa.Float(), nullable=True),
        sa.Column("estimated_cycle_minutes_per_unit", sa.Float(), nullable=True),
        sa.Column("estimated_total_production_minutes", sa.Float(), nullable=True),
        sa.Column("customer_priority", sa.Integer(), nullable=True),
        sa.Column("technical_priority", sa.Integer(), nullable=True),
        sa.Column("commercial_priority", sa.Integer(), nullable=True),
        sa.Column("lateness_penalty_per_day", sa.Float(), nullable=True),
        sa.Column("sla_hours", sa.Float(), nullable=True),
        sa.Column("special_instructions", sa.Text(), nullable=True),
        sa.Column("drawing_approved", sa.Boolean(), nullable=False),
        sa.Column("on_hold", sa.Boolean(), nullable=False),
        sa.Column("hold_reason", sa.Text(), nullable=True),
        sa.Column("depends_on_order_ids", sa.JSON(), nullable=False),
        sa.Column("surface_finish", sa.String(length=128), nullable=True),
        sa.Column("technology", sa.String(length=128), nullable=True),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("order_id"),
    )
    op.create_index(op.f("ix_orders_customer_id"), "orders", ["customer_id"], unique=False)
    op.create_index("ix_orders_customer_status", "orders", ["customer_id", "order_status"], unique=False)
    op.create_index(op.f("ix_orders_due_date"), "orders", ["due_date"], unique=False)
    op.create_index(op.f("ix_orders_external_order_ref"), "orders", ["external_order_ref"], unique=False)
    op.create_index(op.f("ix_orders_machine_group"), "orders", ["machine_group"], unique=False)
    op.create_index(op.f("ix_orders_order_status"), "orders", ["order_status"], unique=False)
    op.create_index(op.f("ix_orders_part_family"), "orders", ["part_family"], unique=False)
    op.create_index(op.f("ix_orders_part_id"), "orders", ["part_id"], unique=False)
    op.create_index(op.f("ix_orders_process_type"), "orders", ["process_type"], unique=False)
    op.create_index(op.f("ix_orders_production_status"), "orders", ["production_status"], unique=False)
    op.create_index(op.f("ix_orders_required_machine_id"), "orders", ["required_machine_id"], unique=False)
    op.create_index(op.f("ix_orders_required_material_id"), "orders", ["required_material_id"], unique=False)
    op.create_index("ix_orders_status_due", "orders", ["order_status", "due_date"], unique=False)
    op.create_table(
        "priority_overrides",
        sa.Column("override_id", sa.String(length=64), nullable=False),
        sa.Column("order_id", sa.String(length=64), nullable=False),
        sa.Column("override_type", sa.String(length=32), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("target_machine_id", sa.String(length=64), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("override_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("released_by", sa.String(length=64), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("override_id"),
    )
    op.create_index(op.f("ix_priority_overrides_active"), "priority_overrides", ["active"], unique=False)
    op.create_index(
        "ix_priority_overrides_order_active", "priority_overrides", ["order_id", "active"], unique=False
    )
    op.create_index(op.f("ix_priority_overrides_order_id"), "priority_overrides", ["order_id"], unique=False)
    op.create_index(
        op.f("ix_priority_overrides_override_type"), "priority_overrides", ["override_type"], unique=False
    )
    op.create_table(
        "priority_profiles",
        sa.Column("row_id", sa.String(length=64), nullable=False),
        sa.Column("profile_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("system_config_version", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("row_id"),
        sa.UniqueConstraint("profile_id", "version", name="uq_priority_profiles_id_version"),
    )
    op.create_index(op.f("ix_priority_profiles_is_active"), "priority_profiles", ["is_active"], unique=False)
    op.create_index(
        op.f("ix_priority_profiles_profile_id"), "priority_profiles", ["profile_id"], unique=False
    )
    op.create_index(
        op.f("ix_priority_profiles_system_config_version"),
        "priority_profiles",
        ["system_config_version"],
        unique=False,
    )
    op.create_table(
        "priority_results",
        sa.Column("result_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("order_id", sa.String(length=64), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("base_score", sa.Float(), nullable=False),
        sa.Column("factors", sa.JSON(), nullable=False),
        sa.Column("adjustments", sa.JSON(), nullable=False),
        sa.Column("readiness", sa.String(length=32), nullable=False),
        sa.Column("blocked", sa.Boolean(), nullable=False),
        sa.Column("blocking_reasons", sa.JSON(), nullable=False),
        sa.Column("risk_level", sa.String(length=32), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("profile_id", sa.String(length=128), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hours_until_due", sa.Float(), nullable=True),
        sa.Column("projected_completion", sa.DateTime(timezone=True), nullable=True),
        sa.Column("projected_lateness_hours", sa.Float(), nullable=True),
        sa.Column("forced_next", sa.Boolean(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("result_id"),
        sa.UniqueConstraint("run_id", "order_id", name="uq_priority_results_run_order"),
    )
    op.create_index(
        op.f("ix_priority_results_computed_at"), "priority_results", ["computed_at"], unique=False
    )
    op.create_index(
        "ix_priority_results_order_computed", "priority_results", ["order_id", "computed_at"], unique=False
    )
    op.create_index(op.f("ix_priority_results_order_id"), "priority_results", ["order_id"], unique=False)
    op.create_index(op.f("ix_priority_results_readiness"), "priority_results", ["readiness"], unique=False)
    op.create_index(op.f("ix_priority_results_risk_level"), "priority_results", ["risk_level"], unique=False)
    op.create_index(op.f("ix_priority_results_run_id"), "priority_results", ["run_id"], unique=False)
    op.create_index("ix_priority_results_run_rank", "priority_results", ["run_id", "rank"], unique=False)
    op.create_index(op.f("ix_priority_results_score"), "priority_results", ["score"], unique=False)
    op.create_table(
        "schedule_locks",
        sa.Column("lock_id", sa.String(length=64), nullable=False),
        sa.Column("lock_type", sa.String(length=32), nullable=False),
        sa.Column("order_id", sa.String(length=64), nullable=True),
        sa.Column("machine_id", sa.String(length=64), nullable=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_reason", sa.Text(), nullable=False),
        sa.Column("sequence_order_ids", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("lock_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("released_by", sa.String(length=64), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("lock_id"),
    )
    op.create_index(op.f("ix_schedule_locks_active"), "schedule_locks", ["active"], unique=False)
    op.create_index(op.f("ix_schedule_locks_lock_type"), "schedule_locks", ["lock_type"], unique=False)
    op.create_index(op.f("ix_schedule_locks_machine_id"), "schedule_locks", ["machine_id"], unique=False)
    op.create_index(op.f("ix_schedule_locks_order_id"), "schedule_locks", ["order_id"], unique=False)
    op.create_table(
        "scheduling_configs",
        sa.Column("row_id", sa.String(length=64), nullable=False),
        sa.Column("config_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("system_config_version", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("row_id"),
        sa.UniqueConstraint("config_id", "version", name="uq_scheduling_configs_id_version"),
    )
    op.create_index(
        op.f("ix_scheduling_configs_config_id"), "scheduling_configs", ["config_id"], unique=False
    )
    op.create_index(
        op.f("ix_scheduling_configs_is_active"), "scheduling_configs", ["is_active"], unique=False
    )
    op.create_index(
        op.f("ix_scheduling_configs_system_config_version"),
        "scheduling_configs",
        ["system_config_version"],
        unique=False,
    )
    op.create_table(
        "sync_runs",
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("connector", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("records_fetched", sa.JSON(), nullable=False),
        sa.Column("records_upserted", sa.JSON(), nullable=False),
        sa.Column("issues_count", sa.Integer(), nullable=False),
        sa.Column("triggered_by", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index(op.f("ix_sync_runs_mode"), "sync_runs", ["mode"], unique=False)
    op.create_index(op.f("ix_sync_runs_started_at"), "sync_runs", ["started_at"], unique=False)
    op.create_index(op.f("ix_sync_runs_status"), "sync_runs", ["status"], unique=False)
    op.create_table(
        "system_configs",
        sa.Column("config_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("config_id"),
        sa.UniqueConstraint("version"),
    )
    op.create_index(op.f("ix_system_configs_is_active"), "system_configs", ["is_active"], unique=False)
    op.create_table(
        "tooling",
        sa.Column("tooling_id", sa.String(length=64), nullable=False),
        sa.Column("tooling_name", sa.String(length=255), nullable=False),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.Column("available_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("compatible_machine_ids", sa.JSON(), nullable=False),
        sa.Column("setup_minutes", sa.Float(), nullable=False),
        sa.Column("expected_life", sa.Float(), nullable=True),
        sa.Column("current_usage", sa.Float(), nullable=True),
        sa.Column("maintenance_status", sa.String(length=64), nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("tooling_id"),
    )
    op.create_table(
        "users",
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=128), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint("username"),
    )
    op.create_index(op.f("ix_users_role"), "users", ["role"], unique=False)
    op.create_table(
        "customer_rules",
        sa.Column("customer_id", sa.String(length=64), nullable=False),
        sa.Column("sla_hours", sa.Float(), nullable=True),
        sa.Column("tier_override", sa.String(length=32), nullable=True),
        sa.Column("priority_boost_points", sa.Float(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.Column("last_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.customer_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("customer_id"),
    )
    op.create_table(
        "machine_downtime",
        sa.Column("downtime_id", sa.String(length=64), nullable=False),
        sa.Column("machine_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["machine_id"], ["machines.machine_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("downtime_id"),
    )
    op.create_index(op.f("ix_machine_downtime_end"), "machine_downtime", ["end"], unique=False)
    op.create_index(op.f("ix_machine_downtime_machine_id"), "machine_downtime", ["machine_id"], unique=False)
    op.create_index(
        "ix_machine_downtime_machine_start", "machine_downtime", ["machine_id", "start"], unique=False
    )
    op.create_table(
        "operations",
        sa.Column("operation_id", sa.String(length=64), nullable=False),
        sa.Column("order_id", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("operation_type", sa.String(length=32), nullable=False),
        sa.Column("machine_group", sa.String(length=128), nullable=True),
        sa.Column("machine_id", sa.String(length=64), nullable=True),
        sa.Column("eligible_machine_ids", sa.JSON(), nullable=False),
        sa.Column("setup_minutes", sa.Float(), nullable=True),
        sa.Column("cycle_minutes_per_unit", sa.Float(), nullable=True),
        sa.Column("machine_cycle_minutes", sa.JSON(), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("completed_quantity", sa.Float(), nullable=False),
        sa.Column("operation_status", sa.String(length=32), nullable=False),
        sa.Column("prerequisite_operation_id", sa.String(length=64), nullable=True),
        sa.Column("material_id", sa.String(length=64), nullable=True),
        sa.Column("material_quantity_per_unit", sa.Float(), nullable=True),
        sa.Column("tooling_ids", sa.JSON(), nullable=False),
        sa.Column("operator_requirement", sa.String(length=128), nullable=True),
        sa.Column("quality_requirement", sa.String(length=128), nullable=True),
        sa.Column("setup_family", sa.String(length=128), nullable=True),
        sa.Column("estimated_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("estimated_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.order_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("operation_id"),
    )
    op.create_index(op.f("ix_operations_machine_group"), "operations", ["machine_group"], unique=False)
    op.create_index(op.f("ix_operations_machine_id"), "operations", ["machine_id"], unique=False)
    op.create_index(op.f("ix_operations_material_id"), "operations", ["material_id"], unique=False)
    op.create_index(op.f("ix_operations_operation_status"), "operations", ["operation_status"], unique=False)
    op.create_index(op.f("ix_operations_operation_type"), "operations", ["operation_type"], unique=False)
    op.create_index(op.f("ix_operations_order_id"), "operations", ["order_id"], unique=False)
    op.create_index("ix_operations_order_sequence", "operations", ["order_id", "sequence"], unique=False)
    op.create_table(
        "optimization_runs",
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("orders_considered", sa.Integer(), nullable=False),
        sa.Column("orders_scheduled", sa.Integer(), nullable=False),
        sa.Column("orders_blocked", sa.Integer(), nullable=False),
        sa.Column("objective_score", sa.Float(), nullable=True),
        sa.Column("quality_score", sa.Float(), nullable=True),
        sa.Column("algorithm", sa.String(length=128), nullable=False),
        sa.Column("algorithm_version", sa.String(length=64), nullable=False),
        sa.Column("profile_id", sa.String(length=128), nullable=True),
        sa.Column("profile_version", sa.Integer(), nullable=True),
        sa.Column("config_version", sa.Integer(), nullable=True),
        sa.Column("input_snapshot_id", sa.String(length=64), nullable=True),
        sa.Column("triggered_by", sa.String(length=64), nullable=True),
        sa.Column("trigger_reason", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["input_snapshot_id"], ["input_snapshots.snapshot_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index(op.f("ix_optimization_runs_kind"), "optimization_runs", ["kind"], unique=False)
    op.create_index(
        "ix_optimization_runs_kind_started", "optimization_runs", ["kind", "started_at"], unique=False
    )
    op.create_index(
        op.f("ix_optimization_runs_started_at"), "optimization_runs", ["started_at"], unique=False
    )
    op.create_index(op.f("ix_optimization_runs_status"), "optimization_runs", ["status"], unique=False)
    op.create_table(
        "schedule_versions",
        sa.Column("schedule_version_id", sa.String(length=64), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("input_snapshot_id", sa.String(length=64), nullable=True),
        sa.Column("algorithm", sa.String(length=128), nullable=False),
        sa.Column("algorithm_version", sa.String(length=64), nullable=False),
        sa.Column("profile_id", sa.String(length=128), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("generated_by", sa.String(length=64), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("horizon_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("horizon_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_by", sa.String(length=64), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by", sa.String(length=64), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("entry_count", sa.Integer(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("quality", sa.JSON(), nullable=True),
        sa.Column("unscheduled", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["input_snapshot_id"], ["input_snapshots.snapshot_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["optimization_runs.run_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("schedule_version_id"),
        sa.UniqueConstraint("version_number"),
    )
    op.create_index(
        op.f("ix_schedule_versions_generated_at"), "schedule_versions", ["generated_at"], unique=False
    )
    op.create_index(op.f("ix_schedule_versions_run_id"), "schedule_versions", ["run_id"], unique=False)
    op.create_index(op.f("ix_schedule_versions_status"), "schedule_versions", ["status"], unique=False)
    op.create_table(
        "schedule_entries",
        sa.Column("row_id", sa.String(length=64), nullable=False),
        sa.Column("schedule_version_id", sa.String(length=64), nullable=False),
        sa.Column("entry_id", sa.String(length=64), nullable=False),
        sa.Column("machine_id", sa.String(length=64), nullable=False),
        sa.Column("order_id", sa.String(length=64), nullable=False),
        sa.Column("operation_id", sa.String(length=64), nullable=False),
        sa.Column("sequence_on_machine", sa.Integer(), nullable=False),
        sa.Column("setup_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("setup_minutes", sa.Float(), nullable=False),
        sa.Column("run_minutes", sa.Float(), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("priority_score", sa.Float(), nullable=False),
        sa.Column("placement_reason", sa.Text(), nullable=False),
        sa.Column("is_last_operation", sa.Boolean(), nullable=False),
        sa.Column("expected_completion", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expected_lateness_hours", sa.Float(), nullable=True),
        sa.Column("locked", sa.Boolean(), nullable=False),
        sa.Column("batch_key", sa.String(length=128), nullable=True),
        sa.Column("setup_family", sa.String(length=128), nullable=True),
        sa.Column("material_id", sa.String(length=64), nullable=True),
        sa.Column("customer_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["schedule_version_id"], ["schedule_versions.schedule_version_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("row_id"),
        sa.UniqueConstraint("schedule_version_id", "entry_id", name="uq_schedule_entries_version_entry"),
    )
    op.create_index(op.f("ix_schedule_entries_end"), "schedule_entries", ["end"], unique=False)
    op.create_index(op.f("ix_schedule_entries_machine_id"), "schedule_entries", ["machine_id"], unique=False)
    op.create_index(op.f("ix_schedule_entries_order_id"), "schedule_entries", ["order_id"], unique=False)
    op.create_index(
        op.f("ix_schedule_entries_schedule_version_id"),
        "schedule_entries",
        ["schedule_version_id"],
        unique=False,
    )
    op.create_index(op.f("ix_schedule_entries_start"), "schedule_entries", ["start"], unique=False)
    op.create_index(
        "ix_schedule_entries_version_machine_start",
        "schedule_entries",
        ["schedule_version_id", "machine_id", "start"],
        unique=False,
    )
    op.create_index(
        "ix_schedule_entries_version_order",
        "schedule_entries",
        ["schedule_version_id", "order_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_schedule_entries_version_order", table_name="schedule_entries")
    op.drop_index("ix_schedule_entries_version_machine_start", table_name="schedule_entries")
    op.drop_index(op.f("ix_schedule_entries_start"), table_name="schedule_entries")
    op.drop_index(op.f("ix_schedule_entries_schedule_version_id"), table_name="schedule_entries")
    op.drop_index(op.f("ix_schedule_entries_order_id"), table_name="schedule_entries")
    op.drop_index(op.f("ix_schedule_entries_machine_id"), table_name="schedule_entries")
    op.drop_index(op.f("ix_schedule_entries_end"), table_name="schedule_entries")
    op.drop_table("schedule_entries")
    op.drop_index(op.f("ix_schedule_versions_status"), table_name="schedule_versions")
    op.drop_index(op.f("ix_schedule_versions_run_id"), table_name="schedule_versions")
    op.drop_index(op.f("ix_schedule_versions_generated_at"), table_name="schedule_versions")
    op.drop_table("schedule_versions")
    op.drop_index(op.f("ix_optimization_runs_status"), table_name="optimization_runs")
    op.drop_index(op.f("ix_optimization_runs_started_at"), table_name="optimization_runs")
    op.drop_index("ix_optimization_runs_kind_started", table_name="optimization_runs")
    op.drop_index(op.f("ix_optimization_runs_kind"), table_name="optimization_runs")
    op.drop_table("optimization_runs")
    op.drop_index("ix_operations_order_sequence", table_name="operations")
    op.drop_index(op.f("ix_operations_order_id"), table_name="operations")
    op.drop_index(op.f("ix_operations_operation_type"), table_name="operations")
    op.drop_index(op.f("ix_operations_operation_status"), table_name="operations")
    op.drop_index(op.f("ix_operations_material_id"), table_name="operations")
    op.drop_index(op.f("ix_operations_machine_id"), table_name="operations")
    op.drop_index(op.f("ix_operations_machine_group"), table_name="operations")
    op.drop_table("operations")
    op.drop_index("ix_machine_downtime_machine_start", table_name="machine_downtime")
    op.drop_index(op.f("ix_machine_downtime_machine_id"), table_name="machine_downtime")
    op.drop_index(op.f("ix_machine_downtime_end"), table_name="machine_downtime")
    op.drop_table("machine_downtime")
    op.drop_table("customer_rules")
    op.drop_index(op.f("ix_users_role"), table_name="users")
    op.drop_table("users")
    op.drop_table("tooling")
    op.drop_index(op.f("ix_system_configs_is_active"), table_name="system_configs")
    op.drop_table("system_configs")
    op.drop_index(op.f("ix_sync_runs_status"), table_name="sync_runs")
    op.drop_index(op.f("ix_sync_runs_started_at"), table_name="sync_runs")
    op.drop_index(op.f("ix_sync_runs_mode"), table_name="sync_runs")
    op.drop_table("sync_runs")
    op.drop_index(op.f("ix_scheduling_configs_system_config_version"), table_name="scheduling_configs")
    op.drop_index(op.f("ix_scheduling_configs_is_active"), table_name="scheduling_configs")
    op.drop_index(op.f("ix_scheduling_configs_config_id"), table_name="scheduling_configs")
    op.drop_table("scheduling_configs")
    op.drop_index(op.f("ix_schedule_locks_order_id"), table_name="schedule_locks")
    op.drop_index(op.f("ix_schedule_locks_machine_id"), table_name="schedule_locks")
    op.drop_index(op.f("ix_schedule_locks_lock_type"), table_name="schedule_locks")
    op.drop_index(op.f("ix_schedule_locks_active"), table_name="schedule_locks")
    op.drop_table("schedule_locks")
    op.drop_index(op.f("ix_priority_results_score"), table_name="priority_results")
    op.drop_index("ix_priority_results_run_rank", table_name="priority_results")
    op.drop_index(op.f("ix_priority_results_run_id"), table_name="priority_results")
    op.drop_index(op.f("ix_priority_results_risk_level"), table_name="priority_results")
    op.drop_index(op.f("ix_priority_results_readiness"), table_name="priority_results")
    op.drop_index(op.f("ix_priority_results_order_id"), table_name="priority_results")
    op.drop_index("ix_priority_results_order_computed", table_name="priority_results")
    op.drop_index(op.f("ix_priority_results_computed_at"), table_name="priority_results")
    op.drop_table("priority_results")
    op.drop_index(op.f("ix_priority_profiles_system_config_version"), table_name="priority_profiles")
    op.drop_index(op.f("ix_priority_profiles_profile_id"), table_name="priority_profiles")
    op.drop_index(op.f("ix_priority_profiles_is_active"), table_name="priority_profiles")
    op.drop_table("priority_profiles")
    op.drop_index(op.f("ix_priority_overrides_override_type"), table_name="priority_overrides")
    op.drop_index(op.f("ix_priority_overrides_order_id"), table_name="priority_overrides")
    op.drop_index("ix_priority_overrides_order_active", table_name="priority_overrides")
    op.drop_index(op.f("ix_priority_overrides_active"), table_name="priority_overrides")
    op.drop_table("priority_overrides")
    op.drop_index("ix_orders_status_due", table_name="orders")
    op.drop_index(op.f("ix_orders_required_material_id"), table_name="orders")
    op.drop_index(op.f("ix_orders_required_machine_id"), table_name="orders")
    op.drop_index(op.f("ix_orders_production_status"), table_name="orders")
    op.drop_index(op.f("ix_orders_process_type"), table_name="orders")
    op.drop_index(op.f("ix_orders_part_id"), table_name="orders")
    op.drop_index(op.f("ix_orders_part_family"), table_name="orders")
    op.drop_index(op.f("ix_orders_order_status"), table_name="orders")
    op.drop_index(op.f("ix_orders_machine_group"), table_name="orders")
    op.drop_index(op.f("ix_orders_external_order_ref"), table_name="orders")
    op.drop_index(op.f("ix_orders_due_date"), table_name="orders")
    op.drop_index("ix_orders_customer_status", table_name="orders")
    op.drop_index(op.f("ix_orders_customer_id"), table_name="orders")
    op.drop_table("orders")
    op.drop_table("materials")
    op.drop_index(op.f("ix_machines_status"), table_name="machines")
    op.drop_index(op.f("ix_machines_process_type"), table_name="machines")
    op.drop_index(op.f("ix_machines_machine_group"), table_name="machines")
    op.drop_index("ix_machines_group_rank", table_name="machines")
    op.drop_table("machines")
    op.drop_index(op.f("ix_input_snapshots_as_of"), table_name="input_snapshots")
    op.drop_table("input_snapshots")
    op.drop_index(op.f("ix_expedites_order_id"), table_name="expedites")
    op.drop_index("ix_expedites_order_active", table_name="expedites")
    op.drop_index(op.f("ix_expedites_expires_at"), table_name="expedites")
    op.drop_index(op.f("ix_expedites_active"), table_name="expedites")
    op.drop_table("expedites")
    op.drop_index(op.f("ix_data_quality_issues_severity"), table_name="data_quality_issues")
    op.drop_index(op.f("ix_data_quality_issues_run_id"), table_name="data_quality_issues")
    op.drop_index("ix_data_quality_issues_run_entity", table_name="data_quality_issues")
    op.drop_index("ix_data_quality_issues_run_code", table_name="data_quality_issues")
    op.drop_index(op.f("ix_data_quality_issues_entity_id"), table_name="data_quality_issues")
    op.drop_index(op.f("ix_data_quality_issues_code"), table_name="data_quality_issues")
    op.drop_table("data_quality_issues")
    op.drop_index(op.f("ix_customers_customer_tier"), table_name="customers")
    op.drop_index(op.f("ix_customers_active"), table_name="customers")
    op.drop_table("customers")
    op.drop_index(op.f("ix_calendar_specs_is_default"), table_name="calendar_specs")
    op.drop_table("calendar_specs")
    op.drop_index("ix_audit_log_user_time", table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_user_id"), table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_timestamp"), table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_entity_type"), table_name="audit_log")
    op.drop_index("ix_audit_log_entity", table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_action"), table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index(op.f("ix_alerts_severity"), table_name="alerts")
    op.drop_index(op.f("ix_alerts_raised_at"), table_name="alerts")
    op.drop_index(op.f("ix_alerts_order_id"), table_name="alerts")
    op.drop_index(op.f("ix_alerts_machine_id"), table_name="alerts")
    op.drop_index(op.f("ix_alerts_alert_type"), table_name="alerts")
    op.drop_index("ix_alerts_active_severity", table_name="alerts")
    op.drop_index("ix_alerts_active_raised", table_name="alerts")
    op.drop_index(op.f("ix_alerts_active"), table_name="alerts")
    op.drop_table("alerts")
