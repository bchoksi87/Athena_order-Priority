"""One module per canonical priority factor (``app.domain.config.FACTOR_KEYS``)."""

from app.engines.priority.factors.batching_affinity import BatchingAffinity
from app.engines.priority.factors.customer_importance import CustomerImportance
from app.engines.priority.factors.delay_penalty import DelayPenalty
from app.engines.priority.factors.downstream_impact import DownstreamImpact
from app.engines.priority.factors.due_date_urgency import DueDateUrgency
from app.engines.priority.factors.machine_availability import MachineAvailability
from app.engines.priority.factors.margin import Margin
from app.engines.priority.factors.order_value import OrderValue
from app.engines.priority.factors.production_readiness import ProductionReadiness
from app.engines.priority.factors.setup_efficiency import SetupEfficiency
from app.engines.priority.factors.sla_risk import SlaRisk

__all__ = [
    "BatchingAffinity",
    "CustomerImportance",
    "DelayPenalty",
    "DownstreamImpact",
    "DueDateUrgency",
    "MachineAvailability",
    "Margin",
    "OrderValue",
    "ProductionReadiness",
    "SetupEfficiency",
    "SlaRisk",
]
