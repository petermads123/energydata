# energydata

APIs for collecting energy data from three sources: Energinet's
[Energi Data Service](https://www.energidataservice.dk/), [Eloverblik](https://eloverblik.dk/)
and the [ENTSO-E Transparency Platform](https://transparency.entsoe.eu/).

## Install as a dependency

```powershell
pip install git+https://github.com/petermads123/energydata.git@main
```

Or in `dependencies` in `pyproject.toml`:

```toml
"energydata @ git+https://github.com/petermads123/energydata.git@main"
```

## Endpoints

Every endpoint returns a `pandas.DataFrame` with a tz-aware `Europe/Copenhagen` index.
Naive `start`/`end` values are read as Danish local time, periods are half-open
`[start, end)`, a lone date means the whole local day and a lone timestamp means one slot.
A slot with no published data is NaN, never filled from a neighbour.

### Energi Data Service

```python
from energydata.energidataservice import get_day_ahead_prices

prices = get_day_ahead_prices("2026-01-15", bidding_zones=["DK1", "DK2"])
```

| Function | Currency and unit | Resolution | Format | Zones | Source datasets |
|---|---|---|---|---|---|
| `get_day_ahead_prices(start, end=None, bidding_zones=("DK1", "DK2"))` | EUR/MWh, excl. VAT | 15 minutes | wide: one float column per zone | `DK1`, `DK2` | *Elspotprices* (hourly, before 2025-10-01 00:00 Danish time, repeated over its four quarter-hours) and *DayAheadPrices* (15 minutes, from that instant; a missing value is NaN, never filled from hourly data) |
| `get_imbalance_prices(start, end=None, bidding_zones=("DK1", "DK2"))` | EUR/MWh | 15 minutes | wide: `(zone, up/down)` columns; one price, repeated in both | `DK1`, `DK2` | *ImbalancePrice* (`ImbalancePriceEUR`), from 2025-03-04 |
| `get_afrr_energy_prices(start, end=None, bidding_zones=("DK1", "DK2"))` | EUR/MWh | 15 minutes | wide: `(zone, up/down)` columns | `DK1`, `DK2` | *ImbalancePrice* (`aFRRVWAUpEUR`, `aFRRVWADownEUR`), from 2025-03-04 |
| `get_mfrr_energy_prices(start, end=None, bidding_zones=("DK1", "DK2"))` | EUR/MWh | 15 minutes | wide: `(zone, up/down)` columns | `DK1`, `DK2` | *MfrrEnergyActivationMarket* (`mFRRSAUpEUR`, `mFRRSADownEUR`), from 2025-03-04 |
| `get_mfrr_capacity_prices(start, end=None, bidding_zones=("DK1", "DK2"), *, include_volumes=False)` | EUR/MW/h (volumes MW) | 1 hour | wide: `(zone, up/down)` columns, plus volume columns | `DK1`, `DK2` | *MfrrCapacityMarket*, from 2023-06-21 |
| `get_afrr_capacity_prices(start, end=None, bidding_zones=("DK1", "DK2"), *, include_volumes=False)` | EUR/MW/h (volumes MW) | 1 hour | wide: `(zone, up/down)` columns, plus volume columns | `DK1`, `DK2` (Nordic rows never returned) | *AfrrReservesNordic*, from 2022-12-08 |
| `get_fcr_n_prices(start, end=None, *, include_volumes=False)` | EUR/MW/h (volumes MW) | 1 hour | wide: `price` column, plus volume columns | `DK2` | *FcrNdDK2* (`FCR-N`, auction `Total`), from 2021-11-10 |
| `get_fcr_d_up_prices(start, end=None, *, include_volumes=False)` | EUR/MW/h (volumes MW) | 1 hour | wide: `price` column, plus volume columns | `DK2` | *FcrNdDK2* (`FCR-D upp`, auction `Total`), from 2021-11-10 |
| `get_fcr_d_down_prices(start, end=None, *, include_volumes=False)` | EUR/MW/h (volumes MW) | 1 hour | wide: `price` column, plus volume columns | `DK2` | *FcrNdDK2* (`FCR-D ned`, auction `Total`), from 2021-11-10 |
| `get_fcr_dk1_prices(start, end=None, *, include_volumes=False)` | EUR/MW/h (volumes MW) | 4-hour blocks (00, 04, ..., 20 Danish time) | wide: `cross_border` and `danish` columns, plus volume columns | `DK1` | *FcrDK1*, from 2021-01-19 |
| `get_ffr_prices(start, end=None, *, include_volumes=False)` | EUR/MW/h (volumes MW) | 1 hour | wide: `price` column, plus volume columns | `DK2` | *FfrDK2*, from 2021-04-26 |

### Tariffs, subscriptions and elafgift

All from *DatahubPricelist* (fields `GLN_Number`, `ChargeType`, `ChargeTypeCode`, `ValidFrom`, `ValidTo`, `ResolutionDuration`, `Price1`-`Price24`), in DKK excl. VAT, from 2014. The hourly functions return a long frame with `start` and `end` columns (tz-aware `Europe/Copenhagen`, one half-open row per hour, 23 or 25 on a DST day) and the value column(s); an hour with no valid row is NaN. The subscription functions return one row per validity period with `start` and `end` clipped to the request. Each function takes an optional `client` (left open if passed).

```python
from energydata.energidataservice import get_dso_tariffs

tariffs = get_dso_tariffs("radius", "2026-10-15")
```

| Function | Currency and unit | Resolution | Format | Source rows |
|---|---|---|---|---|
| `get_dso_tariffs(dso, start, end=None)` | DKK/kWh | 1 hour | long: `start`, `end`, `tariff` | `ChargeType` `D03`, the DSO's GLN and standard C consumption tariff code(s) (table below) |
| `get_energinet_tariffs(start, end=None)` | DKK/kWh | 1 hour | long: `start`, `end`, `system_tariff`, `transmission_tariff` | `D03`, GLN `5790000432752`, codes `41000` and `40000` |
| `get_dso_subscriptions(dso, start, end=None)` | DKK/month | one row per validity period | long: `start`, `end`, `subscription` | `D01`, the DSO's GLN and standard C consumption subscription code(s) (table below) |
| `get_energinet_subscriptions(start, end=None)` | DKK/month | one row per validity period | long: `start`, `end`, `subscription` | `D01`, GLN `5790000432752`, code `41004` |
| `get_electricity_tax(start, end=None)` | DKK/kWh | 1 hour | long: `start`, `end`, `electricity_tax` | `D03`, GLN `5790000432752`, code `EA-001` (normal rate; the reduced electric-heating rate is not published in this dataset) |

`dso` is one of the names below, matched case-insensitively; an unknown name raises `ValueError` listing them. The same table is `energydata.energidataservice.DSOS`. Codes appear in order of precedence: where rows overlap the latest `ValidFrom` wins, then the earlier code. `sunds` publishes no C subscription, so its subscription is NaN.

| Name | DSO | GLN | C tariff code(s) | C subscription code(s) |
|---|---|---|---|---|
| `aal` | Aal El-Net A M B A | 5790001095451 | `AAL-NT-05` | `AAL-E-50` |
| `cerius` | Cerius A/S | 5790000705184 | `30TR_C_ET` | `30AB_CT` |
| `dinel` | Dinel A/S | 5790000610099 | `TCL<100_02` | `ACL<100_01` |
| `elektrus` | Elektrus A/S | 5790000836239 | `6000091` | `6000082` |
| `elinord` | Elinord A/S | 5790001095277 | `43300` | `41300` |
| `elnet-midt` | Elnet Midt A/S | 5790001100520 | `T3001` | `20001` |
| `elvaerk` | Netselskabet Elværk A/S | 5790000681358 | `5NCFF` | `5ACFF` |
| `flow` | FLOW Elnet A/S | 5790000392551 | `FE1 NT-01` | `FE1 E-50` |
| `forsyning-elnet` | Forsyning Elnet A/S | 5790001088309 | `STR-NT-03` | `STR-E-50` |
| `grindsted` | Grindsted Elnet A/S | 5790000681105 | `GEV-NT-01` | `GEV-E-50` |
| `hammel` | Hammel Elforsyning Net A/S | 5790001090166 | `C-Tarif` | `55000` |
| `hjerting` | Hjerting Transformatorforening | 5790001095376 | `C-Tarif` | `HE-E-50` |
| `hurup` | Hurup Elværk Net A/S | 5790000610839 | `HEV-NT-01T` | `HEV-E-50` |
| `ikast` | Ikast El Net A/S | 5790000682102 | `IEV-NT-01` | `IEV-E-50` |
| `kimbrer` | Kimbrer Elnet A/S | 5790001095239 | `C-Tarif` | `AARS-E-50` |
| `konstant-151` | Konstant Net A/S - 151 | 5790000704842 | `151-NT01T`, `C_FBTNTR_B` | `151-E5004`, `C_FBAHM__B` |
| `konstant-245` | Konstant Net A/S - 245 | 5790000683345 | `245-NT01T`, `C_FBTNTR_B` | `245-E5004`, `C_FBAHM__B` |
| `l-net` | L-Net A/S | 5790001090111 | `3000` | `4100` |
| `laesoe` | Læsø Elnet A/S | 5790001103460 | `43100` | `41100` |
| `midtfyns` | Midtfyns Elforsyning A.m.b.A | 5790001089023 | `TNT15000` | `AB15000` |
| `n1-016` | N1 A/S - 016 | 5790002502699 | `C-Tarif` | `K_22000` |
| `n1-131` | N1 A/S - 131 | 5790001089030 | `CD` | `CD` |
| `n1-344` | N1 A/S - 344 | 5790000611003 | `T-C-F-F-TD` | `A-C-F-04` |
| `noe` | NOE Net A/S | 5790000395620 | `30030` | `32310` |
| `nord-energi` | Nord Energi Net A/S | 5790000610877 | `TAC` | `ABC` |
| `radius` | Radius Elnet A/S | 5790000705689 | `DT_C_01` | `DA_C_F_01` |
| `rah` | RAH Net A/S | 5790000681327 | `RAH-C` | `ABON-COPG` |
| `ravdex` | Ravdex A/S | 5790000836727 | `NT-C` | `E-50C1` |
| `sunds` | Sunds Net A.m.b.a | 5790001095444 | `SEF-NT-05` | none |
| `tarm` | Tarm Elværk Net A/S | 5790000706419 | `TEV-NT-01T` | `TEV-E-50` |
| `trefor` | TREFOR El-net A/S | 5790000392261 | `C` | `E-51` |
| `trefor-oest` | TREFOR El-net Øst A/S | 5790000706686 | `46` | `E-50` |
| `veksel` | Veksel A/S | 5790001088217 | `NT-01` | `E-50` |
| `vores-elnet` | Vores Elnet A/S | 5790000610976 | `TNT1009` | `AB1012` |
| `zeanet` | Zeanet A/S | 5790001089375 | `43110` | `41100` |

The functions are plain synchronous calls, also from Jupyter. Several requests for one call
run concurrently, up to a configurable cap.

## Layout

This repo was created from a template and set up with `/repo-setup`.

### Why `src/`

Everything installable lives under `src/`, so nothing else in the repo — `tests/`, `docs/`,
a stray script — can be picked up as a package by accident, and an import in a test resolves
against the installed package rather than against whatever happens to sit in the working
directory.

The trade-off: the repo root is not on `sys.path`, so `python -m <package>.<module>` only
works once you have run `pip install -e ".[dev]"`. `pytest` is unaffected — `pythonpath`
in `pyproject.toml` points it at `src`.

Avoid naming the package folder `lib`, `build`, `dist` or `sdist`: the `.gitignore`
inherited from GitHub's Python template ignores those, so the folder would be silently
untracked even under `src/`.

## Development

### Prerequisites

All installable from PowerShell — no website visits needed:

```powershell
winget install --id Git.Git --source winget
winget install --id Python.Python.3.13 --source winget
code --install-extension ms-python.python
code --install-extension charliermarsh.ruff
```

The Python version must satisfy `requires-python` in `pyproject.toml`. To install a
different one, replace the version in the package id, e.g. `Python.Python.3.14`.

`winget` does not update the PATH of the shell it ran in. Open a new terminal before
continuing, then check with `python --version`.

### Create the environment

From the repo root:

```powershell
python -m venv .venv
```

```powershell
.\.venv\Scripts\Activate.ps1
```

```powershell
python -m pip install --upgrade pip
```

```powershell
pip install -e ".[dev]"
```

The `[dev]` part installs Ruff, mypy and pytest. Without it you get the package only.

In VS Code the environment activates automatically in new terminals once the interpreter is
selected. If it is not picked up, use `CTRL + Shift + P` -> `Python: Select Interpreter` and
choose the one in `.venv`.

### Verify

All four must pass on a fresh clone:

```powershell
ruff check .
```

```powershell
ruff format --check .
```

```powershell
mypy
```

```powershell
pytest
```

### Editor

`.vscode/settings.json` is checked in, so format-on-save, import sorting and Ruff as the
Python formatter are already configured for this repo. Installing the Ruff extension (see
Prerequisites) is all that is required.

## Working with Claude Code

This repo ships a Claude Code configuration under `.claude/`, plus `CLAUDE.md` (a routing
map, loaded every session) and `STRUCTURE.md` (a map of what lives where, imported by
`CLAUDE.md`).

### The commands

These are the ones you would type. Everything else under `.claude/skills/` is a step the
pipeline opens by itself, and you rarely need to know it is there.

| Command | Use it for |
|---|---|
| `/feature <what to build>` | Anything new or changed that is not cosmetic: a module, a public function, a behaviour change. Opens the ten-step pipeline below at step 1. With no argument, reports where an in-flight feature got to. |
| `/fix <symptom>` | Something that exists behaves wrongly: wrong output, a crash, a guard that lets something through. Reproduces it, finds the root cause and sizes what else the cause breaks **before** the pipeline opens, then runs the same ten steps as a fix round. Sends you to `/feature` or `/small-change` instead if the diagnosis says it is not a bug. |
| `/small-change <what to change>` | Cosmetic edits with none of the pipeline: a local rename, a docstring reword, message text, plot styling, formatting. Refuses anything that adds or removes a file, changes a public signature, changes behaviour or needs a new test. |
| `/build` | Resume a build that halted to ask you something, once you have answered. |
| `/recommend` | Re-open step 8, if a session ended with a critical follow-up undecided. |
| `/create-pr` | Open the pull request for a finished branch, if you did not do it in the session that finished it. |
| `/watch-pr` | Resume watching an open pull request in a fresh session. |

You do not have to type any of them: describe the work in prose and Claude picks the route,
saying which and why in one line. The routing rules are in `CLAUDE.md`.

### The implementation pipeline

Anything that is not cosmetic goes through ten steps. You decide at most three times — the
concept, the plan, and any critical follow-up at step 8, which most rounds do not have —
plus a yes before step 9 publishes, and the build in between runs on its own. The state lives on
disk rather than in the conversation, and every step commits and pushes it, so a feature
survives closing the session and coming back tomorrow — one folder per branch, one numbered
file per round:

```
development/
  TEMPLATE.md
  feat/csv-export/
    01-csv-export.md          round 1, shipped
    02-streaming-writer.md    round 2, in flight
```

| Step | Command | Produces | Waits for |
|---|---|---|---|
| 1 | `/conceptualize` | The concept, agreed with you, numbered acceptance criteria, and the branch | you |
| 2 | `/plan` | Modules, full signatures, implementation guide, test intents | you |
| 3 | `/build` → `/implement` | The production code | — |
| 4 | `/build` → `/verify` | ruff, mypy, and a check that the code matches the plan | — |
| 5 | `/build` → `/test` | The edge-case suite, and fixes for what it finds | — |
| 6 | `/build` → `/concept-check` | An audit against step 1, criterion by criterion | — |
| 7 | `/build` → `/ship` | The round closed, whole tree green | — |
| 8 | `/recommend` | Critical follow-ups only, usually none; lesser ideas noted in `DEVELOPMENT.md` | you, only if there is one |
| 9 | `/create-pr` | A full re-verification of the whole branch, then a pull request to `main` | you, before it publishes |
| 10 | `/watch-pr` | An hourly check of the open PR, acting on comments, until it merges or closes | — |

**Steps 3 to 7 run without you.** Once you accept the plan, `/build` runs implement, verify,
test, concept-check and ship in order, each in its own subagent on the model that step
pins, committing and pushing after each. It halts for exactly two things: a finding that
would change the concept you agreed to, and a check that fails twice the same way after one
fix. A halt commits what exists, writes the question into the plan file, and waits; `/build`
resumes once you answer. While it runs you get a trace — a line or two per module, class,
function and test group as each step lands — so you can see what was built without reading
the diff.

Where the work genuinely diverges, more than one agent reads it: a diagnosis critic tries
to falsify the root cause before a fix is agreed, a plan critic reads the plan against the
concept before you accept it, two test designers with different briefs find the edge cases
at step 5, and on a fix round one more reader asks at step 8 where else the same cause
lives. The calling step merges what they find and stays the single voice.

Step 9 requests your review on the PR it opens. **Claude never merges on its own judgment,
and never on an approval alone** — a PR reaches `main` either because you pressed the button
or because you explicitly told Claude to. An approval says the change is wanted, not that it
should ship now. Once told, the instruction still waives nothing: it must not be stale
(anything pushed since means you would be merging code you have not seen), no conflict, and
every review thread resolved. Once a pull request has merged, by either route, Claude
deletes its branch as part of closing out, after confirming the head is on `main`.

Note that **GitHub lets nobody request a review from, or approve, their own pull request**.
In a solo repo, where Claude pushes under your token, every PR is authored by you — so the
review route is unavailable, step 9 assigns you instead, and the merge signal is an explicit
"merge it" from you. `CLAUDE.md` names the approver.

Start with `/feature <what to build>` — it opens step 1. Your confirmation of the concept
opens step 2 in the same turn, and your acceptance of the plan opens the build. After the
build, step 8 asks you only if it found something critical, and step 9 asks before it
publishes.

A bug starts with `/fix <symptom>` instead, and gets a diagnosis before step 1: the symptom
reproduced and its output quoted, the root cause as a file and line, the commit that
introduced it, the other inputs the same cause breaks, and who depends on the current
behaviour — read a second time by a critic whose job is to find a different cause. Only
then does step 1 open, and its first question is the one every fix has: this instance, or
the whole class? The rest of the pipeline is the same, with differences you will see in
the trace: the build writes the reproduction as a test and runs it red before fixing,
and halts if it is not red; the concept check has to show more than a green suite for
"nothing else changed"; and step 8 gets a reader asking where else the same cause lives
and what should have caught it. If the diagnosis finds that the code does what
was agreed and you want something different, `/fix` says so and hands over to `/feature`.

**You do not have to type the commands.** Describe the work in prose — "I want to add CSV
export", "rename that variable" — and Claude classifies it against the small-or-large test
before doing anything: a local rename with no signature or behaviour change is small, and
anything that adds a file, changes a public signature, changes behaviour or needs a test is
not — and a bug is a third thing, routed to `/fix` because the first question it raises is
whether it is a bug at all. It says which way it routed and why in one line, so a wrong
call costs you a sentence to correct, and asks only when the request is genuinely
borderline. When it is close, it routes up to the pipeline, because step 1 is a
conversation you can redirect — whereas a feature handled as a small change quietly skips
the concept, the tests and the audit.

A slash command still wins if you type one, and a question stays a question: asking how
something works gets an answer, not a pipeline.

Each step also picks its own model. Concept, planning and recommendations run on Opus
because they are judgment; implementation, verification, tests, the concept check and the
pull request run on Sonnet at max effort because the thinking has already been done and
written down. Steps 3 to 7 run as subagents for exactly this reason — a skill's model
override lasts the whole turn, so five steps chained in one turn would all run on the first
one's model. The one exception is the end of the build: `/build` opens `/recommend` in its
own turn, so step 8 and the `/create-pr` it hands on to run on the build's model.
`/small-change` runs on Opus too — deciding a change is small enough to skip the pipeline
is the one judgment made without the pipeline to catch it. `CLAUDE.md` has the table.

Step 9 is not a formality. It is the only point where the branch is verified as a whole:
step 7 checked one round at one moment, so on a multi-round branch nothing has yet proved
that round 2 left round 1 working. It re-runs the full suite, every module showcase and
every round's plan against a clean tree and a current `main`, and it marks the plan `done`
in the commit that opens the pull request — so the review lives on the pull request thread
and no commit ever exists just to tidy up afterwards.

Two things are worth knowing about the shape of it. **Step 6 audits against step 1, not
step 2**: a plan can drift from its concept a little at each step while passing every check
along the way, and this is where that gets caught. And **step 8 is deliberately small**:
it raises a follow-up only when leaving it undone would make what ships wrong or unsafe,
and most rounds have none. Lesser ideas become one-line notes in `DEVELOPMENT.md` rather
than questions for you. A recommendation you accept opens a new numbered file in
the same folder and goes back through steps 1 to 7 on the same branch, so one pull request
can carry several deliberate passes over one feature. Step 6 of a later round re-checks the
earlier rounds' acceptance criteria, so a follow-up cannot quietly regress what it builds on.

### Hooks

Four run automatically:

- **Session start** — reports the active plan and its step, so a new session picks up where
  the last one stopped. Silent when nothing is in flight.
- **Before any shell command** — refuses a `git commit` or `git push` that would land on
  `main`, including inside a `&&` chain.
- **After every `.py` write** — Ruff formats and auto-fixes the file; only unfixable issues
  come back.
- **Before a turn ends** — the stop gate. Through step 7 it only reports, because the build
  runs its own checks and has to be able to halt on a red tree; from step 8, and for any
  work with no plan file, it refuses to end a turn that changed Python while ruff, mypy or
  pytest fail or `STRUCTURE.md` is out of sync. Prose-only work is not gated. Create
  `.claude/.skip-gate` to bypass it.

Three caveats worth knowing:

- **Hooks are read at session start.** Editing anything under `.claude/hooks/` or
  `.claude/settings.json` needs a Claude Code restart. Skills and rules hot-reload.
- **The first session prompts for workspace trust**, because `.claude/settings.json`
  registers hooks. Accept it or the hooks stay inactive.
- The hooks call `python` from your PATH and only use the standard library; they locate
  `ruff`, `mypy` and `pytest` inside `.venv` themselves.

### Troubleshooting

`Activate.ps1 cannot be loaded because running scripts is disabled` — allow local scripts
for your user:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

`python` not found right after `winget install` — open a new terminal so PATH is reloaded.
