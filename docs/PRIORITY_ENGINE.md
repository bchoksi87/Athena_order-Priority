# Priority Engine

This document explains how the Priority Engine (`backend/app/engines/priority/`) turns a
`PlanningSnapshot` and a versioned `PriorityProfile` into a ranked, explained priority score per
open order. It covers the scoring model, every factor with its inputs and score curve, the
post-factor adjustments, ranking and fairness, risk derivation, the explanation guarantee, the
weight-change preview, a worked example produced by running the engine, and the reasoning behind
the design. Everything here is derived from the code as it exists; where the code is narrower
than the specification, this is stated.

**How to read this.** `docs/DESIGN_CONTRACT.md` §6.1 fixes the interface; `docs/ARCHITECTURE.md`
§4.1 records the design decision; `docs/DATA_MODEL.md` describes `Order`, `Customer` and the
snapshot the engine reads; `docs/SCHEDULING_ENGINE.md` describes what consumes the results;
`docs/TESTING.md` lists the tests referenced below (`backend/tests/engines/test_priority_*.py`).
Spec phases realised here: 4 (priority factors), 9 (overrides), 14 (preview), 15 (customer rules),
16 (expedite), 17 (fairness), 34 (explainability).

---

## 1. Scoring model

### 1.1 Formula

For every *open* order (`Order.is_open`: status not closed and pending quantity > 0):

```
raw_i        = factor_i.score(order, ctx).raw_score          # clamped to [0, 100]
points_i     = weight_i × raw_i                              # negated when factor.kind == "penalty"
base_score   = Σ points_i
score        = base_score
             + erp_priority + customer_rule + aging + starvation(fairness) + expedite   # in this order
             + overrides (INCREASE / DECREASE / SET_PRIORITY in creation order; FORCE_NEXT last)
score        = clamp(score, 0, 100)
score        = min(score, profile.blocked_order_cap)          # only if blocked, cap configured, not forced
```

Implementation: `engine.py::PriorityEngine._evaluate`, `base.py::make_factor_score`,
`adjustments.py`. Constants `SCORE_MIN = 0`, `SCORE_MAX = 100` live in `explanation.py`.

### 1.2 Weights

`PriorityProfile.weights` is a list of `FactorWeight(key, weight (0..100, percent), enabled,
params)`. `PriorityProfile.weight_map()` keeps the entries that are enabled **and** have
`weight > 0` and divides each by their sum, so the active weights always sum to 1.0. Pydantic
validation refuses a profile with duplicate keys or with no enabled positive weight.

Shipped defaults (`PriorityProfile-A`, version 1):

| Factor key | Raw % | Normalised (all enabled) |
|---|---|---|
| `due_date_urgency` | 25 | 0.25 |
| `customer_importance` | 20 | 0.20 |
| `sla_risk` | 15 | 0.15 |
| `order_value` | 10 | 0.10 |
| `delay_penalty` | 10 | 0.10 |
| `margin` | 5 | 0.05 |
| `production_readiness` | 5 | 0.05 |
| `machine_availability` | 5 | 0.05 |
| `setup_efficiency` | 5 | 0.05 |
| `batching_affinity` | 0 | — (evaluated, not weighted) |
| `downstream_impact` | 0 | — (evaluated, not weighted) |

Notes that follow from the code:

* **Every registered factor is evaluated even when its weight is 0**, so the explanation can show
  the reason with the suffix "(not weighted)". The `FactorScore.weight` is 0 and `points` is 0.
* The normalisation is over *all* enabled positive weights regardless of `kind`.
  `docs/DESIGN_CONTRACT.md` §6.1 phrases it as "bonus weights sum to 1.0"; the two coincide today
  because every shipped factor is of kind `"bonus"` (see §1.3).
* A profile that names a factor key the engine does not have raises a warning
  (`priority.profile.unknown_factors`) and the weight is ignored; a factor the engine has but the
  profile omits gets weight 0.
* Per-factor parameters (`FactorWeight.params`) are read through `factors/common.py::param_float`.
  The only shipped parameter is `batching_affinity.window_orders` (§3.10).

### 1.3 Bonus and penalty factors

`FactorScore.kind` is `"bonus"` or `"penalty"`; `make_factor_score` negates the points of a penalty
factor. **All eleven shipped factors are `"bonus"`.** The spec's "Penalty: −5 — Large setup
required" is therefore *not* produced as a negative line: the large-setup rule lives inside
`setup_efficiency` as a multiplier on the bonus (§3.9), so a large setup shows up as a smaller
positive contribution ("+1.2 — Setup Efficiency: Large setup required (120 min) …"). A
penalty-kind factor can be added without touching the engine (§9); `PriorityEngine.__init__`
rejects duplicate factor keys.

### 1.4 Blocked orders

Readiness comes from the constraint engine (`ConstraintEngine.assessment_map`, see
`docs/SCHEDULING_ENGINE.md` §4.4). `PriorityResult.blocked` is `readiness is not READY`;
`blocking_reasons` are the blocker messages. Blocked orders are **still scored and ranked**: the
`production_readiness` factor lowers their base score (§3.7) and, only if
`PriorityProfile.blocked_order_cap` is set (default `None`), the final score is capped at that
value — except for FORCE_NEXT orders. The cap appears as its own explanation line
("Blocked order: score capped by the priority profile"). The scheduler, not the priority engine,
removes blocked orders from the plan.

### 1.5 Ranking and the fairness top-N cap

`ranking.py::rank_results` sorts all results by the total, deterministic key

```
(not forced_next, -score, due_date is None, due_date, order_id)
```

i.e. forced-next orders first, then score descending, ties broken by earliest due date (orders
without due date last), then order id. Then, when `FairnessConfig.enabled`, `top_n > 0` and
`0 < max_top_n_share_per_customer < 1`, the top-N share rule runs:

* `limit = max(1, floor(top_n × max_top_n_share_per_customer))` (defaults 50 × 0.5 → 25 slots);
* walking the ranking, an order is *kept* in the top-N block while its customer holds fewer than
  `limit` slots, or if it is forced-next; otherwise it is *demoted* to just below the top-N block,
  and receives a zero-point `PriorityAdjustment(kind="fairness")` whose reason documents the
  demotion ("Fairness cap: customer X already holds 25 of the top 50 slots; ranked below them
  (score unchanged)");
* `rank` is assigned 1..n over `kept + demoted + rest`. **Scores are never changed by this pass.**

`evaluate_order()` scores a single order without a rank (ranking needs the population).

### 1.6 Determinism

`PriorityEngine` receives a `Clock`; `computed_at` and every "now" comparison come from it
(`ctx.now` defaults to `snapshot.as_of`). Percentiles are mid-rank (no numpy), dictionaries are
iterated in sorted key order, and the tie-break key above is total, so the same snapshot + profile
produce identical results (`test_ranking_is_deterministic_and_tie_broken_by_due_then_id`).

---

## 2. The priority context

Factors never query the snapshot for snapshot-wide statistics; they read a `PriorityContext`
(`base.py`, the frozen §6.1 contract) or its subclass `ExtendedPriorityContext`
(`context_ext.py`). `PriorityContextBuilder.build()` (`context.py`) fills it in one bulk pass so
each factor is O(1) per order:

| Context field | Computed by | Meaning |
|---|---|---|
| `readiness`, `blockers`, `eligible_machines` | `ConstraintEngine.assessment_map` | State, blockers and eligible machines for each order's next operation |
| `machine_next_free` | `compute_machine_next_free` | Earliest working instant ≥ now per machine (`machine_available_at` + calendar `next_working_time`); machines that are down with no known return are omitted |
| `remaining_minutes` | `estimate_remaining_minutes` | Setup + run over pending operations; next op uses the eligible machines and a state-aware setup, later ops use plausible candidates and base setup; in-progress ops need no setup; falls back to order-level `estimated_total_production_minutes` (pro-rated to pending quantity) or `estimated_cycle_minutes_per_unit`; `None` when unknown, with the gap recorded in `notes[order_id]` |
| `projected_completion` | `project_completion` | Earliest eligible machine's next-free instant, pushed past every blocker's `resolves_at`, plus the remaining minutes of *working* time on that machine's calendar |
| `order_value_percentile`, `margin_percentile`, `penalty_percentile`, `customer_revenue_percentile`, `customer_profitability_percentile`, `downstream_percentile` | `mid_rank_percentiles` | `(#less + #less_or_equal) / 2n` within the open-order (or customer) population; a single value sits at 0.5 |
| `population_max` | builder | Maxima of order value, margin, penalty and downstream value (for `linear`/`log` scaling) |
| `downstream_value`, `critical_path` | `compute_downstream` | Sum of order value of every open transitive dependent (cycles in ERP data are ignored); critical-path reason when a dependent cannot meet its due date unless this order starts now |
| `batching_share`, `batching_detail` | `compute_batching` | Share of the next-due window sharing a batching dimension *and* a machine (§3.10) |
| `weights` | `profile.weight_map()` | Normalised weights, computed once |
| `scheduling_config`, `calendars`, `notes` | builder | Setup rules for estimates; calendars; data-gap notes |

The builder takes the constraint engine and calendars as optional constructor arguments so the
simulation engine can hand it the *same* calendars and constraint engine the scheduler uses.
Missing ERP data never raises here: estimates become `None` and the factors fall back to their
documented defaults.

---

## 3. Factors

All factors live in `backend/app/engines/priority/factors/`, one module each, and are registered in
canonical order in `registry.py` (`FACTOR_KEYS` in `app/domain/config.py`). Each `score()` returns a
`FactorScore(key, name, kind, raw_score, weight, points, reason, details)`; `details` holds the
numbers the reason was rendered from. Config classes are in `app/domain/config.py`.

### 3.1 `due_date_urgency` — Due Date Urgency

*Inputs:* `order.due_date` (effective: revised > promised > requested), `ctx.now`,
`ctx.projected_completion[order]` when `DueDateThresholds.use_projected_lateness` (default on).
*Config:* `PriorityProfile.due_date` (`DueDateThresholds`).

Effective horizon `h` = hours until due; when a projected completion exists,
`h = min(hours_until_due, due − projected_completion)` (the slack). Score curve
(`urgency_score`): `h ≤ 0 → overdue_score`; otherwise piecewise-linear through the anchors
`(critical_hours, critical_score) → (high_days·24, high_score) → (medium_days·24, medium_score) →
(low_days·24, low_score)`; below the first anchor the first value holds; beyond the last anchor the
last slope continues down to `floor_score`.

| Hours to effective due | Default score |
|---|---|
| ≤ 0 (overdue or projected late) | 100 (`overdue_score`) |
| 0 < h ≤ 24 | 95 (`critical_score`) |
| 36 | 87.5 |
| 48 | 80 (`high_score`) |
| 168 (7 d) | 50 (`medium_score`) |
| 336 (14 d) | 25 (`low_score`) |
| ≈ 470 (19.6 d) and beyond | 5 (`floor_score`, reached by continuing the last slope) |

Reasons: "Overdue by 30 hours", "Due in 5.0 days but projected 24 hours late", "Due in 18 hours,
projected completion leaves 17 hours slack", "Due in 18 hours". *Missing data:* no due date →
`floor_score` with reason "No due date (data quality issue)".
Tests: `TestDueDateUrgency` incl. two Hypothesis properties (bounded in [0,100] and monotone
non-increasing in `h`; any monotone configuration stays in range).

### 3.2 `sla_risk` — SLA Risk

*Inputs:* SLA hours resolved in precedence `order.sla_hours → customer rule → customer.sla_hours →
SlaRiskConfig.default_sla_hours` (`resolve_sla_hours`); SLA clock starts at `received_date` (else
`order_date`); `ctx.projected_completion`. *Config:* `PriorityProfile.sla`.

`remaining = sla_hours − elapsed`, and when a projected completion exists
`remaining = min(remaining, deadline − projected_completion)`; `ratio = remaining / sla_hours`
maps through `(0, breach) → (imminent_ratio, imminent) → (watch_ratio, watch) → (1, safe)`:

| Remaining ratio | Default score |
|---|---|
| ≤ 0 (breached, or projected past the deadline) | 100 |
| 0.125 | 92.5 |
| 0.25 (`imminent_ratio`) | 85 |
| 0.5 (`watch_ratio`) | 55 |
| ≥ 1.0 | 15 (`safe_score`) |

Reasons: "SLA 48 h: 36 hours remaining (75%)", "SLA 24 h breached by 6 hours", "SLA 48 h:
projected completion misses deadline by 4 hours". *Missing data:* no SLA anywhere → raw 0, "No SLA"
(the default profile has `default_sla_hours=None`); SLA known but no received/order date →
`watch_score` (55) with "elapsed time unknown". Note the clock starts at receipt: an order received
nine days ago with a 48 h SLA is reported as breached even if its due date is still ahead (see the
worked example).

### 3.3 `customer_importance` — Customer Importance

*Inputs:* `Customer.customer_tier` (overridden by an active `CustomerRule.tier_override`),
`strategic_customer_flag`, `escalation_level`, the customer's revenue and profitability percentiles.
*Config:* `PriorityProfile.customer` (`CustomerScoring`).

```
tier_score  = tier_scores[tier]                       # strategic 100, key 75, standard 45, low 20
blended     = weighted mean of (1 − revenue_weight − profitability_weight) × tier_score,
              revenue_weight × 100·revenue_pct, profitability_weight × 100·profitability_pct
              (a missing percentile drops out of the mean instead of pulling it down)
raw         = min(max_score, blended + strategic_flag_bonus·[strategic] + escalation_points_per_level × level)
```

Defaults: weights 0.5 / 0.3 / 0.2, strategic bonus 10, 8 points per escalation level, cap 100.
Reason: "strategic customer Aero Dynamics (tier set by customer rule), revenue top 25%, strategic
account, escalation level 2". *Missing data:* unknown customer id → lowest tier score with
"Unknown customer C-9 (data quality issue)".

### 3.4 `order_value` — Order Value

*Inputs:* `order.order_value`, `ctx.order_value_percentile`, `population_max`. *Config:*
`PriorityProfile.order_value` (`OrderValueConfig`: `scaling` = `percentile` (default) | `linear` |
`log`, `cap_value`, `min_score` = 5).

`raw = min_score + (100 − min_score) × f` where `f` is the percentile, `value/cap` (linear) or
`log1p(value)/log1p(cap)` (log); `cap` is `cap_value` or the population maximum; linear/log without
any cap fall back to the percentile. *Missing data:* unknown value → `min_score`, "Order value
unknown"; single-order population → percentile 0.5. Fairness against high-value orders starving
others is handled by the aging/starvation adjustments, not here.

### 3.5 `margin` — Contribution Margin

*Inputs:* `estimated_margin`, else `actual_margin`; `ctx.margin_percentile`. `raw = 100 ×
percentile`. Reason: "Estimated margin 12,000 (top 30% of open orders)"; negative margins say so.
*Missing data:* raw 0, "Margin unknown".

### 3.6 `delay_penalty` — Delay Penalty

*Inputs:* `order.lateness_penalty_per_day` (ERP) else `order_value ×
default_penalty_per_day_ratio` (1 %); customer escalation level and strategic flag. *Config:*
`PriorityProfile.delay_penalty`.

```
penalty/day = base × (1 + escalation_multiplier_per_level × level) × (strategic_multiplier if strategic else 1)
raw         = 100 × min(1, penalty / reference_penalty)      # if reference_penalty configured
            = 100 × penalty_percentile                        # otherwise (default)
```

`effective_penalty_per_day` is shared with the context builder so the percentile population and the
per-order number use one formula. Reason: "Estimated penalty 3,750/day (top 50% of open orders),
escalated x1.50". *Missing data:* no ERP penalty and no order value → raw 0.

### 3.7 `production_readiness` — Production Readiness

*Inputs:* `ctx.readiness[order]`, `ctx.blockers[order]`, `order.material_status`. *Config:*
`PriorityProfile.readiness` (`ReadinessScoring`).

| Readiness state | Default raw |
|---|---|
| READY | 100 |
| WAITING_MATERIAL with `material_status == PARTIAL` | 40 |
| WAITING_MATERIAL | 10 |
| WAITING_TOOLING | 15 |
| WAITING_APPROVAL | 5 |
| WAITING_PREVIOUS_OPERATION | 35 |
| MACHINE_UNAVAILABLE | 20 |
| QUALITY_HOLD, ON_HOLD, OTHER_CONSTRAINT | 0 (`hold_score`) |

The reason lists up to two blockers with their expected resolution date ("Waiting for material:
material AL-7075: need 20 kg, 4 free, 30 incoming, expected 14 Sep (+1 more)"). *Missing data:*
readiness not assessed (plain context) → `ready_score`, "Readiness not assessed (assumed ready)".

### 3.8 `machine_availability` — Machine Availability

*Inputs:* `ctx.eligible_machines[order]`, `ctx.machine_next_free`. *Config:*
`PriorityProfile.machine_availability`.

`wait` = hours until the earliest eligible machine is free:

| Wait | Default raw |
|---|---|
| ≤ 0 (free now) | 100 |
| 4 h | 80 (linear between 100 and `available_soon_score` up to `available_within_hours` = 8) |
| 8 h | 60 |
| 16 h | 30 (`60 × 8 / wait`, hyperbolic decay) |
| very long | 5 (`none_available_score` floor) |

If exactly one machine can make the part and it is free now, `single_machine_bonus` (15) is added
(the total is clamped to 100 by `make_factor_score`). *Missing data:* no eligible machine → 5, "No
eligible machine"; eligible machines but none with a known return → 5, "… availability unknown".

### 3.9 `setup_efficiency` — Setup Efficiency

*Inputs:* the order's next operation, each eligible machine's current setup family / material /
mounted tooling, `SchedulingConfig.setup` via `estimate_setup` (the same function the scheduler and
the `setup_changeover` soft constraint use). *Config:* `PriorityProfile.setup`.

The eligible machine with the smallest estimated setup (ties: basis rank, then id) decides the
basis: `same_family → 100`, `same_material → 70`, `changeover → 20`, machine state unknown → 50.
Large setups scale the raw score down: for `minutes > large_setup_minutes` (90),
`factor = 1 − min(1, (minutes − 90)/90) × large_setup_penalty_score/100`, so 120 min → ×0.667,
≥ 180 min → ×0. Reasons: "Same material on CNC-01 (same material 'AL')", "Changeover on CNC-02:
60 min setup", "Large setup required (120 min) on CNC-01; …". *Missing data:* no eligible machine →
`unknown_score`.

### 3.10 `batching_affinity` — Batching Affinity

*Inputs:* `ExtendedPriorityContext.batching_share` — of the next `window_orders` open orders by
due date (factor param, default `fairness.top_n` = 50), the share that shares at least one
configured `SchedulingConfig.batching.dimensions` value (`material`, `part_family`, `customer`,
`surface_finish`, `technology`, `process`; `machine`/`tool`/`fixture` have no order-level value)
*and* at least one eligible machine with this order. `raw = 100 × share`. Reason: "2 of the next 3
orders due share material on CNC-01, CNC-02". Weight 0 by default: batching is primarily a
scheduler concern. *Missing data:* plain context → 0, "No batching data".

### 3.11 `downstream_impact` — Downstream Impact

*Inputs:* `ctx.downstream_value`, `snapshot.dependents_of(order)`, `critical_path`,
`downstream_percentile`. On a critical chain → 100 ("On critical path: dependent O-9 due in 20
hours needs 30 hours"); otherwise `100 × percentile` of downstream value ("2 dependent order(s)
worth 180,000"); no dependents → 0. Weight 0 by default because `Order.depends_on_order_ids` is an
ERP field of unknown availability (`docs/REQUIREMENTS.md` U-16).

---

## 4. Adjustments

Applied by `adjustments.py` in this exact order after `base_score`; each produces a
`PriorityAdjustment(kind, points, reason, source_id)` that is rendered verbatim.

| # | Kind | Rule (defaults) | Reason text |
|---|---|---|---|
| 1 | `erp_priority` | `profile.erp_priority_points[str(order.erp_priority)]`: `{"1": 10, "2": 5, "3": 0, "4": −3, "5": −6}`; omitted when the code is unknown or maps to 0 | "ERP priority 2" |
| 2 | `customer_rule` | `CustomerRule.priority_boost_points` of an active rule for the customer (0 → omitted) | "Customer rule for C-AERO: key account" |
| 3 | `aging` | `AgingConfig`: days waited (since `received_date`, else `order_date`) beyond `start_after_days` (5) × `points_per_day` (2), capped at `max_points` (20) | "Waiting 9 days (4 beyond 5)", "…, capped at 20" |
| 4 | `fairness` (starvation) | `FairnessConfig`: `starvation_boost_points` (25) once waiting ≥ `max_wait_days` (10) | "Starvation prevention: waiting 12 days (threshold 10)" |
| 5 | `expedite` | strongest active `Expedite` for the order (`starts_at ≤ now < expires_at`): `min(boost_points, ExpediteConfig.max_boost_points = 60)` | "Expedited by manager: rush (expires in 4 hours), capped at 60" |
| 6 | `override` | active `PriorityOverride`s in creation order: INCREASE adds `+|value|`, DECREASE adds `−|value|`, SET_PRIORITY adds `value − running score`; FORCE_NEXT is applied last and adds `100 − running score`, setting `forced_next`; overrides without a value are logged and skipped; HOLD/RELEASE/MOVE/LOCK_MACHINE_ASSIGNMENT are not priority changes and are ignored here | "Priority raised by manager: customer call", "Priority set to 42 by …", "Forced next by …" |

Also recorded as adjustments but *after* scoring: the zero-point `fairness` demotion from the
ranking pass (§1.5). `waiting_days` returns `None` when neither date exists, which disables aging
and starvation for that order. Expedites and overrides expire on their own; the audit trail is the
service layer's concern (`docs/ARCHITECTURE.md` §3.4).

Practical consequence of the ordering: SET_PRIORITY replaces everything accumulated before it
(factors, ERP, rule, aging, starvation, expedite) but a later INCREASE/DECREASE still applies on
top; FORCE_NEXT always yields exactly 100 and rank 1 (subject only to other forced orders).

---

## 5. Risk level

`engine.py::assess_risk(order, ctx, RiskThresholds)` returns `(risk, hours_until_due,
projected_completion, projected_lateness_hours, basis)`:

| Condition (evaluated top-down) | Risk | Basis |
|---|---|---|
| no due date | LOW | `no_due_date` |
| `hours_until_due < 0` | CRITICAL | `overdue` |
| projected completion known and `lateness > critical_lateness_hours` (0) | CRITICAL | `projected_late` |
| projected completion known: `slack = −lateness` | — | `projected_slack` |
| else remaining minutes known: `slack = hours − remaining/60`; `slack < critical_lateness_hours` → CRITICAL | CRITICAL | `cannot_finish_in_time` |
| else `slack = hours_until_due` | — | `time_to_due_only` |
| `slack < high_slack_hours` (8) | HIGH | |
| `slack < medium_slack_hours` (48) | MEDIUM | |
| otherwise | LOW | |

`PriorityResult` carries `risk_level`, `hours_until_due`, `projected_completion` and
`projected_lateness_hours` (test `test_assess_risk_levels`).

---

## 6. Explanation (spec Phase 34)

`explanation.py` renders text **only from the computed `FactorScore` and `PriorityAdjustment`
lists** — never from a parallel calculation:

```
ORDER #R3D-10482
Priority: 96
Why?
+23.8 — Due Date Urgency: Due in 18 hours, projected completion leaves 17 hours slack
…
+8 — Aging: Waiting 9 days (4 beyond 5)
Total: 96
Blocked: <reasons>                       # only when blocked
Forced next by planner override          # only when forced
```

* `explanation_lines(result)` returns the structured lines (`kind` = `factor` | `adjustment` |
  `cap`, plus `key`, `label`, `points`, `reason`, and for factors `raw_score`/`weight`) used by the
  API and the text renderer; zero-weight factors carry "(not weighted)".
* Two synthetic lines keep the arithmetic visible: `clamp` ("Score limited to 0..100 (raw total
  112.3)") when the raw total left [0, 100], and `blocked_cap` when the blocked-order cap applied.
* **Guarantee:** `Σ points of explanation_lines(result) == result.score` (to 1e-9) and the displayed
  rounded numbers re-sum to the score within display precision —
  `test_lines_re_sum_to_the_score`. `render_explanation` is a pure function of the result
  (`test_render_is_pure_function_of_result`): mutating a factor's points and re-rendering shows a
  `Cap` line rather than a silently inconsistent text.
* `format_points`: `+28`, `-3`, `+27.4`, `0` (integers without decimals, one decimal otherwise).
* The engine renders after ranking (`PriorityEngine.evaluate`), so the fairness demotion line is
  included; `evaluate_order` renders immediately.

The frontend `ExplanationPanel` renders these lists verbatim and composes no reasons of its own
(`frontend/README.md`).

---

## 7. Profile preview (spec Phase 14)

`preview.py::compare_profiles(snapshot, profile_a, profile_b, engine, top_n=50, customer_rules)`
evaluates both profiles on the same snapshot and returns a `ProfileComparison`:

* `top_n_a`, `top_n_b`, `entered_top_n` (in B's top-N but not A's, ordered by B rank),
  `left_top_n`, `rank_deltas` (`rank_b − rank_a`, negative = moved up), `score_deltas`,
  `orders_changed_rank`, `mean_abs_score_delta`, `max_abs_score_delta`, `weight_changes`
  (raw percent pairs for keys that differ; a disabled factor counts as 0);
* `summary`: "Changing Due Date Urgency weight from 30% to 40% would move 27 order(s) into the top
  50 and 27 out" (up to three weight changes are named; otherwise "Switching from profile A to B …");
* `details["context_reused"]`: when only weights differ (`only_weights_differ` compares the models
  excluding `weights`, `profile_id`, `name`, `version`) the expensive context is built once and
  reused with `dataclasses.replace(ctx, profile=B, weights=B.weight_map())`; a threshold change
  rebuilds it.

The preview does not persist anything. It is exposed as
`POST /api/v1/priority/configuration/preview` (`app/api/v1/priority_config.py`, backed by
`ConfigService.preview_profile(candidate, top_n) -> ProfileComparison`, which delegates to
`compare_profiles`); the engine function is tested in `test_priority_preview.py`.

---

## 8. Worked example

Produced by `backend/.venv/bin/python` on the snapshot below (three single-operation CNC orders,
two machines with an 08:00–16:00 UTC weekday calendar, default `PriorityProfile()`, clock frozen
at Monday 2026-09-07 08:00 UTC). The snapshot was built directly from the domain dataclasses
(`PlanningSnapshot(as_of=NOW, customers=…, orders=…, operations=…, machines=…, calendars=…,
default_calendar_id="CAL")` followed by `rebuild_indexes()`):

```python
NOW = datetime(2026, 9, 7, 8, 0, tzinfo=UTC)
customers = [
    Customer("C-AERO", "Aero Dynamics", customer_tier=CustomerTier.STRATEGIC, strategic_customer_flag=True,
             customer_revenue=1_200_000.0, sla_hours=48.0),
    Customer("C-STD", "Standard Parts Ltd", customer_tier=CustomerTier.STANDARD, customer_revenue=150_000.0),
]
orders = [  # order(id, customer, due_hours, value, received_days_ago, **extra); qty 10, RELEASED
    order("R3D-10482", "C-AERO", due_hours=18,     value=250_000.0, received_days_ago=9, erp_priority=2),
    order("O-2",       "C-STD",  due_hours=5 * 24, value=40_000.0,  received_days_ago=2),
    order("O-3",       "C-STD",  due_hours=12 * 24, value=800_000.0, received_days_ago=1),
]
# one CNC operation per order: setup 30 min, 6 min/unit, material "AL"; CNC-01 currently holds "AL"
engine = PriorityEngine(default_factors(), FrozenClock(NOW))
results = engine.evaluate(snapshot, PriorityProfile())
```

Real output:

```
rank  order       score   base   risk      readiness  projected_completion
1     R3D-10482     95.5   82.5  medium    ready      2026-09-07T09:15:00+00:00
2     O-3           45.8   45.8  low       ready      2026-09-07T09:15:00+00:00
3     O-2           40.3   40.3  low       ready      2026-09-07T09:15:00+00:00

ORDER #R3D-10482
Priority: 96
Why?
+23.8 — Due Date Urgency: Due in 18 hours, projected completion leaves 17 hours slack
+15 — SLA Risk: SLA 48 h breached by 7.1 days
+20 — Customer Importance: strategic customer Aero Dynamics, revenue top 25%, strategic account
+5.2 — Order Value: Order value 250,000 (top 50% of open orders)
0 — Contribution Margin: Margin unknown
+5 — Delay Penalty: Estimated penalty 3,750/day (top 50% of open orders), escalated x1.50
+5 — Production Readiness: Ready to run
+5 — Machine Availability: Machine CNC-01 available now
+3.5 — Setup Efficiency: Same material on CNC-01 (same material 'AL')
0 — Batching Affinity: 2 of the next 3 orders due share material on CNC-01, CNC-02 (not weighted)
0 — Downstream Impact: No dependent orders (not weighted)
+5 — ERP priority: ERP priority 2
+8 — Aging: Waiting 9 days (4 beyond 5)
Total: 96

factor breakdown for R3D-10482 (key, raw, weight, points):
  due_date_urgency       raw=  95.0  w=0.250  points=+23.75
  sla_risk               raw= 100.0  w=0.150  points=+15.00
  customer_importance    raw= 100.0  w=0.200  points=+20.00
  order_value            raw=  52.5  w=0.100  points= +5.25
  margin                 raw=   0.0  w=0.050  points= +0.00
  delay_penalty          raw=  50.0  w=0.100  points= +5.00
  production_readiness   raw= 100.0  w=0.050  points= +5.00
  machine_availability   raw= 100.0  w=0.050  points= +5.00
  setup_efficiency       raw=  70.0  w=0.050  points= +3.50
  batching_affinity      raw= 100.0  w=0.000  points= +0.00
  downstream_impact      raw=   0.0  w=0.000  points= +0.00
```

Reading the numbers against the rules above:

* Due date: 18 h to due and a projected completion at 09:15 leave 17 h slack, inside the first
  anchor → raw 95 (§3.1) × 0.25 = 23.75.
* SLA: received 9 days ago with a 48 h SLA → breached → raw 100 × 0.15 = 15 (§3.2 note on the
  clock start).
* Customer: tier strategic → 100; revenue percentile 0.75 (mid-rank of two customers) → 75;
  profitability is unknown and drops out of the blend, so
  blended = (0.5 × 100 + 0.3 × 75) / 0.8 = 90.6; plus the strategic bonus 10 = 100.6, capped at
  `max_score` 100; escalation level 0.
* Order value: 250,000 is the middle of three → percentile 0.5 → 5 + 95 × 0.5 = 52.5.
* Delay penalty: no ERP penalty → 1 % of 250,000 = 2,500/day × 1.5 (strategic) = 3,750/day;
  percentile 0.5 → raw 50.
* Base 82.5 + ERP priority 5 + aging (9 − 5) × 2 = 8 → 95.5; displayed "Priority: 96" is the
  rounded score, and the lines re-sum to 95.5 within display precision.
* Risk MEDIUM: projected slack 17 h is between the 8 h and 48 h thresholds.

---

## 9. Adding a new factor

1. Add the key to `FactorKey`, `FACTOR_KEYS` and `FACTOR_NAMES` in `app/domain/config.py`, and a
   `FactorWeight(key=..., weight=...)` to `PriorityProfile.weights` defaults (a config model for its
   thresholds goes next to the others; engines must not embed numbers).
2. Create `app/engines/priority/factors/<key>.py` with a class exposing `key`, `name`,
   `kind` (`"bonus"` or `"penalty"`) and `score(order, ctx) -> FactorScore`; obtain the weight with
   `context_ext.factor_weight(ctx, self.key)` and build the result with `make_factor_score` (it
   clamps the raw score and signs the points). Return a raw score and an explicit reason for every
   missing-data path — never raise.
3. If the factor needs snapshot-wide data, add a field with a default to `ExtendedPriorityContext`
   and compute it in `PriorityContextBuilder.build` (bulk, once per pass); read it through
   `context_ext.extended(ctx)` so the factor degrades gracefully on a plain context.
4. Register the class in `registry._FACTOR_CLASSES` and export it from `factors/__init__.py`.
5. Tests in `tests/engines/test_priority_factors.py`: curve anchors, reasons, missing data;
   `test_registry_covers_every_key_and_rejects_unknown` and `test_every_factor_handles_empty_order`
   pick the factor up automatically. Nothing in `engine.py`, `explanation.py` or `ranking.py`
   changes.

---

## 10. Why the design is what it is

* **No single formula (spec Phase 4).** Each factor is an isolated module that computes one
  number from one concern and states its own reason. Trade-offs between concerns are expressed
  only through weights the planner edits, and the linear form means "+25 — Due in 18 hours" is a
  literal statement of the arithmetic. A multiplicative or rule-cascade model could not print a
  breakdown that re-sums.
* **Configurability.** Every threshold, anchor and bonus is a field of `PriorityProfile` (versioned
  in the database — `docs/DATA_MODEL.md` §9), normalisation keeps the scale stable when factors are
  disabled, and per-factor `params` allow new knobs without schema changes. Missing-data fallbacks
  are configured scores (`floor_score`, `min_score`, `unknown_score`, `watch_score`) rather than
  code constants.
* **Explainability (spec Phase 34).** The renderer consumes the computed lists and nothing else;
  the clamp and blocked-cap lines exist so that the printed numbers always add up; the ranking pass
  documents itself with a zero-point adjustment rather than silently reordering.
* **Fairness (spec Phase 17).** Aging and starvation are additive adjustments that grow with
  waiting time and are visible in the breakdown; the top-N share cap works on ranks (not scores),
  so no customer can occupy the whole queue head while the scores remain honest.
* **Human decisions win (spec Phases 9, 16).** Overrides are applied last, FORCE_NEXT pins the
  score at 100, expedites are bounded in points and duration by configuration and expire on their
  own, and every one of them is a labelled line in the explanation.
* **Robust to ERP gaps.** Percentile scaling makes value, margin and penalty comparable without
  currency assumptions; every factor has a defined behaviour and reason when its input is absent,
  so the Data Quality Engine reports the gap while the queue stays usable.
* **Deterministic and cheap.** One bulk context pass, O(1) per factor per order, an injected clock,
  and a total sort key: 600 orders evaluate in well under five seconds in the engine test, and a
  weight change re-ranks without rebuilding the context.

### Known limitations

* No shipped penalty-kind factor; large setups reduce a bonus instead of producing a negative line.
* `blocked_order_cap` is off by default, so a blocked order can outrank ready ones in the queue
  (the scheduler still skips it); enable the cap in the profile if the queue view should reflect
  schedulability.
* The SLA clock always starts at receipt, and the customer-importance revenue rank is relative to
  the customers present in the snapshot, not to the whole customer base.
* The projected completion is a naive single-machine projection built before scheduling; the
  scheduler's `expected_completion` is authoritative once a schedule exists.
* Endpoints exist for the queue (`GET /api/v1/orders`), the explanation
  (`GET /api/v1/orders/{id}/explanation`), machine options, overrides/expedites and the preview
  (`app/api/v1/orders.py`, `priority_config.py`), but no service yet runs the end-to-end
  `PlanningPipeline` (`app/engines/pipeline.py`, `docs/SCHEDULING_ENGINE.md` §2.1) to produce and
  persist a planning run; `app/workers` is empty.
