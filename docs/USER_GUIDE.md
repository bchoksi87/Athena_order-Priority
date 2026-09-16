# PPSE Operating Guide

How to run a production day with the Production Priority & Scheduling Engine: who signs in, what to
do in which order, what every screen is for, and what to do when the shop floor changes. The same
procedures apply to the browser demo and to a full deployment against your ERP.

---

## 1. Before you start

**Two ways to run the tool**

| | Browser demo | Full system |
|---|---|---|
| Where | https://claude.ai/artifact/RunyHw12mkoKoYHn42y7y9 | `docker compose up --build`, then http://localhost:8080 |
| Data | A synthetic plant (80 customers, 300 order lines, 12 machines) captured from the real engines | Live data from the ERP connector (mock connector with a 5,000-line plant until the real ERP is connected) |
| What is live | Everything except what-if runs, replanning and analytics, which are served from pre-computed presets | Everything |
| Your changes | Kept in your browser; **Reset demo data** on the login page restores the baseline | Stored in PostgreSQL with a full audit trail |

**Accounts.** Six accounts are seeded, one per role. In the demo, press the account name on the login
page to sign in with one click.

| Username | Password | Role | Can do |
|---|---|---|---|
| `admin` | `admin123` | Admin | Everything, including saving configuration versions and managing users |
| `manager` | `manager123` | Production Manager | Approve, publish and reject plans; expedite, override, force next, move, lock; customer rules |
| `planner` | `planner123` | Planner | Generate drafts, evaluate replans, run what-if, hold and release orders, preview weight changes |
| `supervisor` | `supervisor123` | Supervisor | Read every board, acknowledge alerts |
| `operator` | `operator123` | Operator | Read the queue and machine boards |
| `executive` | `executive123` | Executive | Read-only dashboards and analytics |

Every change that affects priorities or the plan asks for a **reason** and is written to the Audit Log
with your user, the time, the previous value and the new value.

---

## 2. The daily operating cycle

Follow these steps every morning; steps 7 and 8 continue through the day.

### Step 1: Refresh the data from the ERP

- In a full deployment the worker syncs every 15 minutes (full or incremental). To force a sync:
  **System Administration → Run sync** (admin). The runs table shows fetched and upserted counts,
  issues and reconciliation deltas.
- The **Required / Available / Missing** table on the same screen shows which ERP fields the engines
  can see. A field marked Missing weakens the factor that needs it (for example no cycle times means no
  projected completion).

### Step 2: Clear data-quality blockers

- Open **Data Quality**. The headline reads "N orders cannot be scheduled because:" followed by the
  causes (missing cycle time, missing due date, missing machine, invalid routing, impossible times).
- Blocking issues keep an order out of the schedule; warnings do not. Fix the source record in the ERP,
  then rerun the check with **Run data quality check** (planner) or wait for the next sync.

### Step 3: Generate the plan

- **Control Tower → Generate schedule** (planner or manager). The engine scores every open order,
  places every operation on a machine and returns a **draft** version (v2, v3 …) with its quality
  score, on-time %, utilisation, setup efficiency, average lateness and orders at risk.
- The active plan is unchanged until a draft is approved and published. The plan bar shows the newer
  draft next to the active plan with a metric-by-metric comparison.

### Step 4: Read the Control Tower

Top to bottom, the screen answers the ten morning questions:

1. **Plan bar**: active version and status, quality, the newer draft and the comparison table.
2. **KPI strip**: open orders, due today/tomorrow/this week, overdue, at risk with revenue at risk,
   blocked (by material, tooling, machine, approval), expected on-time versus historical,
   utilisation versus demanded capacity, unscheduled.
3. **Produce next**: the top of the ranked queue with due date, pending quantity, machine and score.
4. **What is blocking production**: blocked orders by cause plus the data-quality headline.
5. **Where is the bottleneck**: resource, utilisation, orders waiting, capacity shortfall, revenue at
   risk and the recommended action.
6. **Capacity gaps**: required versus available hours per machine group for the horizon.
7. **Orders likely to miss delivery**: projected completion after the due date, with lateness and value.
8. **Recommended actions**: the alert engine's suggestions grouped by type.

Red edges mark tiles that need attention. Every card links to the detail screen behind it.

### Step 5: Investigate and intervene

- **Priority Queue**: the complete ranked list. Filter by customer, due window, machine group,
  process, risk, readiness, status or on-hold; sort by any column; export the page as CSV.
- Press **Why?** on a row. The drawer lists every factor with its points and reason
  ("+25 Due Date Urgency: Due in 18 hours", "+20 Customer Importance: Strategic account"), the
  adjustments (aging, starvation, expedite, override) and the total. The numbers are produced by the
  same code that ranked the order.
- Open the order for the full picture: customer and commercial data, route and operations, schedule
  entries, machine options with reasons, dependencies, active overrides, audit trail.
- Intervene with the **Actions** menu (see section 4). Each action re-ranks the queue immediately.

### Step 6: Check the machine plan

- **Machine Schedule**: per-machine board by day (timeline), week, or the list format
  "08:00 — Setup — Job 1045 / 12:00 — Break". Filter by group, process or machine set; pick a version.
- **Gantt Schedule**: all machines, day/week/fortnight zoom, colour by status or customer, late-only
  and locked-only filters. **Compare with** a previous version to see moved entries as ghost bars
  and the metric deltas.
- Open a machine for its calendar, downtime windows, compatible materials and tooling, locks and
  its own timeline.

### Step 7: Approve and publish

- **Approve vN** (production manager) on the plan bar or under the newer-draft panel; give a reason.
  The draft becomes **approved**; the previously approved version is superseded.
- **Publish vN** (production manager) sends the approved plan through the ERP writeback ladder and
  makes it the active plan. The writeback badge in the top bar shows the mode:
  - **READ ONLY** (default): the plan becomes active in PPSE only; a skipped receipt is recorded.
  - **APPROVAL** and **WRITEBACK**: the approved schedule is sent to the ERP.
  - **CONTROLLED AUTO**: the worker publishes automatically within the configured rules.
- **Reject vN** discards a draft with a reason. Rejected and superseded versions stay in the version
  history for traceability.

### Step 8: Run the day

- **Alerts**: overdue, likely late, SLA breach risk, machine downtime, material or tool shortage,
  capacity overload, bottleneck, production behind schedule, starvation, data quality. Each alert has a
  severity, the order or machine, the reason and a recommended action. Acknowledge with a note
  (supervisor and above); acknowledged alerts stay visible in the history.
- **Evaluate replan** (planner) whenever something changes: the engine detects events since the
  active plan (new orders, completions, machines down, material arrivals, delays, quality failures),
  generates a candidate, applies the stability rules and reports a decision with the old-versus-new
  comparison. By default a candidate that should replace the plan waits for approval as a new draft.
  In a full deployment the worker does this every 30 minutes.
- Stability rules protect the shop floor: entries starting inside the frozen window (30 minutes by
  default) are never moved, the lock window (4 hours by default) is reproduced verbatim, and a replan
  is only proposed when quality improves by at least 3% or an event makes the current plan infeasible.
- Operators and supervisors work from **Machine Schedule** and the **Machine detail** timeline;
  executives from the **Executive Dashboard**.

### Step 9: Trace decisions

- **Audit Log** answers "why was this order scheduled at 2:30 pm yesterday": filter by entity, user,
  action or date; every row shows the previous and new value as a diff and the reason given.
- **Order detail → Audit trail** shows the same rows for one order; the plan bar shows who generated,
  approved and published each version.

---

## 3. Screen-by-screen reference

| Screen | Purpose | Main controls |
|---|---|---|
| Control Tower | The morning view; plan workflow | Generate, Approve, Publish, Reject, Evaluate replan; Produce next with Why? and quick actions |
| Priority Queue | Every open order ranked | Filters, sort, Why? drawer, quick actions, column picker, CSV export |
| Order detail | One order end to end | Expedite, Hold/Release, Override priority, Force next, Move to machine, Lock machine assignment; cancel overrides; audit trail |
| Machine Schedule | Machine-centric board | Day / week / list views, group and process filters, version selector |
| Gantt Schedule | Whole-plant timeline | Zoom, filters, colour by, version selector, compare mode, entry panel |
| Machine detail | One machine | Calendar and shifts, downtime, capabilities, locks, timeline |
| Executive Dashboard | KPIs and trends | On-time trend and breakdowns, quality components, active plan, bottleneck, alert counters |
| Bottleneck Analysis | Where capacity is short | Ranked bottlenecks, utilisation by group / machine / process, drill-down to waiting orders |
| Capacity Planning | Required versus available hours | Dimension (machine, group, process, department), period (day, week), horizon |
| What-If Simulation | Try a change without touching the plan | Scenario builder, run, before/after diff, saved scenario sets |
| Priority Configuration | Weights and thresholds | Sliders, sub-config forms, Preview impact, Save (admin), versions and rollback |
| Scheduling Configuration | Horizon, locking, stability, setup, batching, objectives, alerts, data-quality severities | Section forms, versions, activate |
| Alerts | Exceptions needing attention | Filters, acknowledge with note, summary counters |
| Data Quality | Why orders cannot be scheduled | Headline, cause breakdown, issues table, Run data quality check |
| Audit Log | Every decision | Filters, previous → new diff viewer |
| System Administration | Users, sync, health | Users, health and metrics, sync runs, field capabilities, Run sync |

---

## 4. Order actions reference

All actions are in **Order detail → Actions** and, for the most common ones, in the row menu of the
Priority Queue and the Control Tower. Every action requires a reason.

| Action | Who | Effect | Default |
|---|---|---|---|
| Expedite | Manager | Adds boost points for a limited time, then normal scoring resumes | +30 points for 4 h (maximum 60 points, 72 h) |
| Hold / Release | Planner | Hold marks the order blocked (readiness "on hold"); Release removes the hold. ERP-held orders cannot be released here | Held orders are capped at 70 points |
| Override priority | Manager | Increase or decrease by N points, or set an absolute score; optional expiry | |
| Force next | Manager | Rank 1 with score 100 until cancelled; only one order can be forced at a time | |
| Move to machine | Manager | Pins the order to a chosen eligible machine (optional time), creating a lock; the picker shows eligible and rejected machines with reasons | |
| Lock machine assignment | Manager | Keeps the current machine assignment across replans | |
| Cancel | Manager | Any active override, expedite or lock can be cancelled from the order's overlay list | |

Time-slot, machine and sequence locks (for example "lock the next 4 hours") are available through
the schedule lock API and are honoured by every replan; the lock window default is 240 minutes.

---

## 5. Configuration procedures

### Priority weights (Priority Configuration)

1. Adjust the sliders (due date urgency, customer importance, SLA risk, order value, margin, delay
   penalty, production readiness, machine availability, setup efficiency, batching affinity,
   downstream impact). The stacked bar shows the normalised shares.
2. Open a sub-configuration tab to change thresholds (due-date anchors, customer tiers, SLA ratios,
   readiness scores, aging, fairness, expedite limits, risk thresholds, blocked-order cap).
3. Press **Preview impact** (planner and above). You get a sentence such as "Changing Due Date
   Urgency weight from 25% to 40% would move 27 orders into the top 50" with the orders entering and
   leaving the top N and their rank changes.
4. **Save** (admin) with a reason. A new version is created; the audit row stores only the keys that
   changed. Every order is re-scored.
5. **Versions**: activate an older version to roll back.

### Scheduling rules (Scheduling Configuration)

Horizon (14 days), lock window (240 min), stability (frozen window 30 min, minimum improvement 3%),
setup factors and default setup time, batching dimensions and limits, objective weights, overtime,
machine preference costs, quality-score weights, replanning triggers and approval requirement,
alert thresholds, data-quality severities. Saving creates a new version; the next generated draft
uses it.

### Customer rules (Customers)

Per customer: SLA hours, tier override, priority boost, notes. Used by the SLA-risk and
customer-importance factors.

### Users (System Administration)

Create users with a role, deactivate, reset passwords. Change the seeded passwords before exposing
a deployment.

---

## 6. What-if simulation procedure

1. Open **What-If Simulation** (planner and above).
2. Add one or more scenarios: machine down (machine, start, hours), urgent orders, add machine
   (clone of an existing one), extra working day or shift, outsource quantity, material delay or
   arrival, prioritise customer, weight change, due date change, hold orders, expedite orders.
3. Press **Run**. The baseline and the scenario are scheduled with the same engine; the real plan is
   not touched.
4. Read the result: the management summary sentence, before/after strip (orders affected, late
   orders, on-time %, average lateness, utilisation, setup hours, revenue and margin at risk, extra
   overtime), bottlenecks before and after, and the affected orders with completion deltas and
   machine changes.
5. **Save scenario** keeps the set in your browser for reuse.

In the demo, one result per scenario kind was pre-computed by the engine; the summary says
"Demo preset" when it is served from that set.

---

## 7. Situations and what to do

| Situation | Steps |
|---|---|
| A machine breaks down | Machine detail shows status and downtime. What-If → machine down to see the impact; Evaluate replan for a candidate plan; approve and publish it. Orders that must not move: Move to machine or Lock machine assignment first |
| An urgent order arrives | After the next sync it appears in the queue with its score. If it must run now: Expedite (time-limited) or Force next; then Evaluate replan |
| Material is late | The order shows "waiting material" and is excluded from the plan; when the expected receipt date is known the readiness factor shows it. Use What-If → material delay to quantify the effect; Hold if the customer agrees to a new date |
| A customer escalates | Customers → rule with a boost or tier override (affects all their orders), or Expedite the specific order. Priority Configuration → Preview impact shows the ranking effect of a weight change |
| Many orders cannot be scheduled | Data Quality: fix missing cycle times, machines, due dates or routings in the ERP; the headline tells you which fields |
| The plan changes too often | Scheduling Configuration: raise the minimum improvement or the frozen window, keep "require approval" on; use locks for the near term |
| Why was this scheduled here? | Order detail → Why? (score), Machine options (why this machine), Audit trail (who changed what); Audit Log for plant-wide history |

---

## 8. Understanding the numbers

- **Priority score (0–100)** = sum over factors of normalised weight × factor score, plus adjustments
  (ERP priority, customer rule, aging after 5 days at 2 points per day up to 20, starvation boost
  of 25 after 10 days, expedite, overrides), clamped to 0–100. Blocked orders are capped at 70 unless
  forced next. Ranking: forced-next first, then score, then due date.
- **Risk**: critical when overdue or projected late; high when slack is under 8 hours; medium under
  48 hours; otherwise low.
- **Readiness**: ready, waiting material, waiting tooling, waiting approval, waiting previous
  operation, machine unavailable, quality hold, on hold.
- **Schedule quality (0–100)**: on-time delivery, lateness, utilisation, setup efficiency and at-risk
  share, weighted as configured.
- **Capacity utilisation above 100%** means more hours are demanded inside the horizon than the
  calendars provide; the bottleneck screen shows where.

---

## 9. Full deployment quick start

```bash
cp .env.example .env            # set secrets and the ERP connector
docker compose up --build       # db → migrate → seed + sync → backend, worker, frontend
```

UI at http://localhost:8080, API docs at http://localhost:8000/docs. Details, environment variables
and the writeback rollout are in `docs/DEPLOYMENT.md`; connecting a real ERP is described in
`docs/ERP_INTEGRATION.md`.
