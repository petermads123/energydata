# Shared HTTP utilities

<!-- claude-plan step=2 status=active -->

| Field | Value |
|---|---|
| Feature | `feat/shared-http-utils` |
| Round | `1` |
| Branch | `feat/shared-http-utils` |
| Started | `2026-10-06` |

## Progress

| # | Step | Skill | Runs | Status |
|---|---|---|---|---|
| 1 | Conceptualize | `/conceptualize` | with the user | done |
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

Nothing — this is the first round.

---

## 1. Concept

### What this is

An `energydata.utils` subpackage that the three source subpackages (Energi Data Service,
Eloverblik, ENTSO-E Transparency Platform) build on. Three parts:

- **A retrying request wrapper**, sync and async with identical behaviour, on `httpx`.
  Transient failures are retried with exponential backoff and jitter; a holdoff the server
  states (a `Retry-After` header, or a value a caller-supplied function reads from the
  response) replaces the computed delay.
- **Response readers** that turn JSON, XML, CSV and ZIP bodies into plain Python data.
- **A date-range chunker** ("stepback" in the sense of splitting a too-large request) that
  splits a period an API refuses as too long into windows and runs a fetch per window.

### Why it is worth building

All three sources need the same handling of rate limits and outages, and ENTSO-E and
Eloverblik both cap the period one request may cover. Without a shared layer each
subpackage grows its own slightly different copy.

### Inputs and outputs

- **Wrapper:** an `httpx` client (or one it creates), method, URL and the usual request
  arguments; returns the `httpx.Response` on success. Configurable: maximum attempts, base
  delay, maximum delay, the longest server-requested holdoff it will honour (default five
  minutes), and an optional function reading a holdoff from a response, for APIs that put
  it in the body rather than a header.
- **Readers:** bytes or a response in; out: JSON → `dict`/`list`, XML → `ElementTree`
  element, CSV → `list[dict[str, str]]`, ZIP → `{member name: parsed content}` with each
  member parsed by its file extension and an unknown extension returned as raw bytes.
  Plain Python rather than DataFrames because much of the data is not tabular (nested
  ENTSO-E time series, Eloverblik metering-point records); each source converts to tables
  where the data is a table.
- **Chunker:** timezone-aware `start` and `end` and a maximum span (`timedelta`) → the
  list of windows. The chunked fetch (sync and async) calls a caller's function once per
  window and returns the results in window order; joining them is source-specific.

### How it connects to the rest of the repo

Nothing in the package calls it yet; the future `energidataservice`, `eloverblik` and
`entsoe` subpackages are its callers. It does not touch `hello_world`, which stays as the
placeholder for now. `httpx` becomes the first runtime dependency in `pyproject.toml`.

### Explicitly out of scope

- Authentication — Eloverblik's refresh/access-token exchange, ENTSO-E's security token.
- Endpoint URLs and any source-specific parameters.
- Interpreting ENTSO-E's acknowledgement (error) documents.
- DataFrames and any tabular conversion.
- Proactive rate limiting (throttling before the server objects).
- Caching.
- ZIP-bomb or decompression-size limits.
- Running chunk windows concurrently: the async chunked fetch runs windows one at a time,
  so splitting a request does not multiply the load on the server.

Assumptions: a holdoff longer than the cap raises immediately rather than sleeping for it;
XML is parsed with the standard library.

### Acceptance criteria

| # | The finished feature... |
|---|---|
| A1 | Retries connection errors, timeouts, HTTP 429 and HTTP 500/502/503/504 up to the maximum number of attempts and returns the first successful response; any other 4xx or other error status is raised on the first attempt without retrying. |
| A2 | Without a server-stated holdoff, waits before retry *n* a delay that grows exponentially from the base delay, with random jitter, and never exceeds the maximum delay; all three are configurable. |
| A3 | When a response carries `Retry-After` (seconds or an HTTP date), or a supplied holdoff function returns a value, waits that long instead of the computed delay; a holdoff longer than the cap raises immediately with an error stating the requested wait. |
| A4 | When attempts are exhausted, raises one specific error type carrying the attempt count and the last response or exception. |
| A5 | The sync and async wrappers behave identically for A1 to A4, and the async one waits without blocking the event loop. |
| A6 | The readers parse JSON, XML, CSV and ZIP as described in Inputs and outputs; reading a response picks the format from its `Content-Type`, and the format can also be named explicitly; a malformed body raises an error naming the format. |
| A7 | The chunker returns contiguous half-open windows, each at most the span long (the last may be shorter), together covering exactly `[start, end)`; `start >= end`, a naive datetime, or a non-positive span raises `ValueError`. |
| A8 | The sync and async chunked fetch call the function once per window, in order, and return the results in window order; an error in any window propagates. |
| A9 | The test suite makes no real network calls, and `httpx` is the only new runtime dependency. |

### Open questions

None.

---

## 2. Plan

> Written in step 2, accepted by the user before step 3 starts. Concrete enough that
> step 3 is transcription, not invention.

### Approach

One paragraph on the chosen approach, and one on what was rejected and why.

### Modules

| Path | New or changed | Purpose |
|---|---|---|

### Public API

> Every public class and function, with its full signature as it will be written.
> `Covers` links back to the acceptance criteria above.

| Signature | Module | Purpose | Covers |
|---|---|---|---|

### Implementation guide

Ordered. Each entry small enough to finish and check.

1.
2.

### Test intents

> High-level: what a test must prove, not how it is written. Step 5 turns each of these
> into concrete cases, including the edge cases.

| # | Must prove | Covers |
|---|---|---|
| T1 | | |

### Risks

What could make this harder than it looks, and what the build should do if it does —
including whether it should halt.

---

## 3. Implementation notes

> Written in step 3. Only deviations from the plan above, each with its reason. "Built as
> planned" is a complete and good entry. On a fix round, also the reproduction test's red
> run, pasted here before the fix was written — step 6 cites it.

---

## 4. Verification log

> Written in step 4: the static half. Command output, not a summary of it.

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

> Written in step 5: the dynamic half.

| Intent | Test names | Result |
|---|---|---|

Edge cases considered and deliberately skipped, with reasons:

---

## 6. Concept check

> Written in step 6, against section 1 — not against section 2. The question is whether
> the thing built is the thing agreed, not whether it matches the plan.

| # | Criterion | Met | Evidence |
|---|---|---|---|
| A1 | | | |

Drift found, and what was done about it:

### Earlier rounds still hold

> Later rounds only. Re-check every acceptance criterion from every earlier round in this
> folder: this round changed code they depend on, and their tests passing is necessary but
> not sufficient — a criterion can be satisfied by tests that no longer describe what the
> feature does.

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

> Written in step 8. Only follow-ups that are critical and belong to this work, which most
> rounds do not have: replace the table with `None.` when there are none. Lesser ideas are
> one-line notes in `DEVELOPMENT.md`, not rows here. Not bugs in what this round built —
> those go back through `/build` before the pull request. A critical defect outside what
> section 1 promised, such as a class member it put out of scope, is a recommendation here,
> and its round opens through `/fix`.

| # | Recommendation | Why it is critical | Effort | Decision |
|---|---|---|---|---|
| R1 | | | | |

Decisions: `deferred`, `rejected`, or `next round` — a new numbered file in this folder,
taken back through steps 1 to 7 on the same branch.

---

## 9. Pull request

> Written in step 9, in the commit that opens the pull request — so the URL is not known
> yet and the pull request is found from the branch. The review itself is recorded on the
> pull request thread, not here: this file is `done` from step 9 on.

| Field | Value |
|---|---|
| URL | opened by step 9 — see the branch's pull request |
| Opened as | ready for review |

---

## Halted

> Only if the build stopped. Written by `/build`: the step, the reason verbatim from the
> step that halted, the question for the user — and, once answered, the answer and what
> changed because of it. Never deleted; it is the record of where the plan was thinner
> than the code needed.
