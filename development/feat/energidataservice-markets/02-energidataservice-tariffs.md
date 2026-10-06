# Energi Data Service tariffs, subscriptions and elafgift

<!-- claude-plan step=1 status=active -->

| Field | Value |
|---|---|
| Feature | `feat/energidataservice-markets` |
| Round | `2` |
| Branch | `feat/energidataservice-markets` |
| Started | `2026-10-06` |

## Progress

| # | Step | Skill | Runs | Status |
|---|---|---|---|---|
| 1 | Conceptualize | `/conceptualize` | with the user | pending |
| 2 | Plan | `/plan` | with the user | pending |
| 3 | Implement | `/implement` | in `/build` | pending |
| 4 | Verify | `/verify` | in `/build` | pending |
| 5 | Test | `/test` | in `/build` | pending |
| 6 | Concept check | `/concept-check` | in `/build` | pending |
| 7 | Ship | `/ship` | in `/build` | pending |
| 8 | Recommend | `/recommend` | with the user | pending |
| 9 | Pull request | `/create-pr` | with the user | pending |
| 10 | Review | `/watch-pr` | on the pull request | pending |

Statuses: `pending`, `in progress`, `done`.

## Builds on

| Round | File | What it delivered |
|---|---|---|
| 1 | `01-energidataservice-markets.md` | Ten market-price functions (`balancing.py`, `reserves.py`) on a private shared path (`_markets.py`), `block_index` and `combine_levels` in `utils.frames`, the probe fixture and `MarketsService` mock; then the package version set to 1.0.0. |

This round came from recommendation `R1` of round 1, which read:

> Tariffs, subscriptions and elafgift (DSO and Energinet C-customer tariffs, subscriptions, electricity tax) from *DatahubPricelist*, as round 2 on this branch and PR — user's request (after PR #4 opened).

What is already on the branch that this round must not break: the ten market functions and
their 1484-test suite, `get_day_ahead_prices` and the client from PR #3, the `utils` helpers
(`block_index`, `combine_levels` included), and `version = "1.0.0"`.

---

## 1. Concept

### Defect

| Field | Value |
|---|---|
| Observed | What happens, quoted from the reproduction. |
| Expected | What should happen, and what says so — a docstring, a test, an earlier round's criterion. |
| Reproduction | The exact command or call and its output. Step 3 turns this into the first test and runs it red before fixing. |
| Root cause | `file.py:NN`, and the decision on that line that is wrong. |
| Introduced by | The commit, or "older than the history here". |
| Class | Other inputs the same cause breaks, and the same shape elsewhere in the repo. |
| Blast radius | Callers of the cause, tests that will move, anything that depends on the current behaviour. |
| Scope | `this instance` or `the class` — the user's decision, with the reason. What the class holds that is not taken goes under Explicitly out of scope by name. |

Critique — the `diagnosis-critic`'s findings and what was done with each:

### What this is

### Why it is worth building

### Inputs and outputs

### How it connects to the rest of the repo

Which existing modules it calls, which call it, what it does not touch.

### Explicitly out of scope

### Acceptance criteria

| # | The finished feature... |
|---|---|
| A1 | |
| A2 | |

### Open questions

---

## 2. Plan

### Approach

One paragraph on the chosen approach, and one on what was rejected and why.

### Modules

| Path | New or changed | Purpose |
|---|---|---|

### Public API

| Signature | Module | Purpose | Covers |
|---|---|---|---|

### Implementation guide

Ordered. Each entry small enough to finish and check.

1.
2.

### Test intents

| # | Must prove | Covers |
|---|---|---|
| T1 | | |

### Risks

What could make this harder than it looks, and what the build should do if it does —
including whether it should halt.

---

## 3. Implementation notes

---

## 4. Verification log

| Check | Result |
|---|---|
| `ruff check .` | |
| `ruff format --check .` | |
| `mypy` | |
| Plan completeness | every signature in the Public API table exists as written |
| `STRUCTURE.md` | in sync |
| `python -m <package>.<module>` | |

---

## 5. Test log

| Intent | Test names | Result |
|---|---|---|

Edge cases considered and deliberately skipped, with reasons:

---

## 6. Concept check

| # | Criterion | Met | Evidence |
|---|---|---|---|
| A1 | | | |

Drift found, and what was done about it:

### Earlier rounds still hold

| Round | # | Criterion | Still met | Evidence |
|---|---|---|---|---|

---

## 7. Ship log

| Field | Value |
|---|---|
| Commits | |
| Pushed to | |

---

## 8. Recommendations

| # | Recommendation | Why it is critical | Effort | Decision |
|---|---|---|---|---|
| R1 | | | | |

Decisions: `deferred`, `rejected`, or `next round` — a new numbered file in this folder,
taken back through steps 1 to 7 on the same branch.

---

## 9. Pull request

| Field | Value |
|---|---|
| URL | opened by step 9 — see the branch's pull request |
| Opened as | ready for review |

---

## Halted

