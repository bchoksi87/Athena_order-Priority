"""Factor ``batching_affinity`` (spec Phase 4 §10): how many upcoming orders this one batches with.

The context builder indexes the next ``window_orders`` open orders by due
date and, for every open order, counts the window members that share a
batching dimension (material or part family) *and* at least one eligible
machine. The share of the window (0..1) is the raw score. Weight is 0 in
the default profile: batching is primarily a scheduler concern ("must never
blindly override critical customer deadlines"); enabling the weight nudges
similar jobs together in the queue.
"""

from __future__ import annotations

from typing import Any, Literal

from app.domain.config import FACTOR_NAMES
from app.domain.models import Order
from app.domain.results import FactorScore
from app.engines.priority.base import PriorityContext, make_factor_score
from app.engines.priority.context_ext import extended, factor_weight

KEY = "batching_affinity"
WINDOW_PARAM = "window_orders"


class BatchingAffinity:
    key = KEY
    name = FACTOR_NAMES[KEY]
    kind: Literal["bonus", "penalty"] = "bonus"

    def score(self, order: Order, ctx: PriorityContext) -> FactorScore:
        weight = factor_weight(ctx, self.key)
        ext = extended(ctx)
        if ext is None or order.order_id not in ext.batching_share:
            return make_factor_score(self, 0.0, weight, "No batching data", {})
        share = ext.batching_share[order.order_id]
        detail: dict[str, Any] = dict(ext.batching_detail.get(order.order_id, {}))
        peers = int(detail.get("peers", 0))
        window = int(detail.get("window", 0))
        if peers <= 0:
            reason = f"No similar orders among the next {window} due"
        else:
            dims = detail.get("dimensions") or []
            what = " / ".join(str(d) for d in dims) if dims else "setup"
            machines = detail.get("shared_machine_ids") or []
            where = f" on {', '.join(str(m) for m in machines[:3])}" if machines else ""
            reason = f"{peers} of the next {window} orders due share {what}{where}"
        return make_factor_score(self, 100.0 * share, weight, reason, detail)


__all__ = ["KEY", "WINDOW_PARAM", "BatchingAffinity"]
