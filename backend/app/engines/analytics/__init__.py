"""Analytics engines (DESIGN_CONTRACT §6.6): executive KPIs, capacity, bottlenecks, OTD, risk and alerts."""

from app.engines.analytics.alerts import ALERT_RULES, build_alert_context, evaluate_alerts
from app.engines.analytics.bottleneck import find_bottlenecks
from app.engines.analytics.capacity import CapacityReport, CapacityTotals, capacity_rows, compute_capacity
from app.engines.analytics.kpis import compute_executive_kpis
from app.engines.analytics.otd import OtdBucket, OtdReport, OtdTrendPoint, on_time_delivery
from app.engines.analytics.risk import OrderRisk, RiskReport, orders_at_risk

__all__ = [
    "ALERT_RULES",
    "CapacityReport",
    "CapacityTotals",
    "OrderRisk",
    "OtdBucket",
    "OtdReport",
    "OtdTrendPoint",
    "RiskReport",
    "build_alert_context",
    "capacity_rows",
    "compute_capacity",
    "compute_executive_kpis",
    "evaluate_alerts",
    "find_bottlenecks",
    "on_time_delivery",
    "orders_at_risk",
]
