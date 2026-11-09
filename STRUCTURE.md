# Structure

Map of everything in this repo. Loaded into context at the start of every session via the
`@STRUCTURE.md` import in `CLAUDE.md`, so it is what Claude uses to find things without
searching.

## Keeping this file current

It describes the code on `main`. During a feature it is left alone: each round lists what it
adds or changes in the "Changes" section of its `log.md`, and `/finish` brings it up to date
once per feature, in the commit that opens the pull request (`docs-writer`), for every one of:

- a module is added, deleted, renamed or moved
- a public function or class is added or removed
- a public signature changes (parameters, defaults, return type)
- a module's purpose changes

Private helpers (names starting with `_`) are intentionally left out. They are
implementation detail, and listing them is what makes a file like this rot.

`.claude/hooks/check_sync.py` (run by `/finish` and by CI) cross-checks the module paths
named here against the `.py` files on disk and fails on a mismatch. It only sees file-level
drift; signature drift is the docs-writer's to catch, reading the code.

That covers `src/`, `tests/` and `.claude/hooks/`. The hooks are documented here with full
signature tables, type-checked like the package (`[tool.mypy] files` names all three) and
importable from the suite (`[tool.pytest.ini_options] pythonpath` names `src` and
`.claude/hooks`, so a test imports a hook by module name the same way a sibling hook does
at runtime), so they are held to the same standard despite not being installable.

## Growth

While the package is flat, keep everything here. Once it has subpackages, keep the tree and
one line per subpackage in this file, and move per-subpackage detail into
`.claude/rules/structure-<subpackage>.md` with `paths: ["<subpackage>/**"]` so it loads only
when Claude works in that subpackage. Split rather than delete — there is no length limit
here, but everything in this file is in context every session.

## Tree

```
src/                    everything installable; nothing outside it is packaged
  energydata/           the package itself
    utils/              shared utilities: API client base, retrying requests, body readers, date-range chunking, periods, zones, frames
    energidataservice/  Energi Data Service: client, day-ahead, balancing and reserve market prices, tariffs and tax
    minstroem/          Min Strøm Consumer API: client, addresses, consumption, solar export, costs, prices and price forecast
    eloverblik/         Eloverblik Customer API: client, metering points and details, meter data, charges
tests/                  pytest suite, one test_<module>.py per module, prefixed test_<subpackage>_ where a name would collide (test_minstroem_*.py, test_eloverblik_*.py); fixtures/ holds recorded payloads and the Min Strøm and Eloverblik OpenAPI specs
development/            the pipeline's state: one folder per feature branch (see its README); the older folders hold the numbered plan files of the pipeline before 2.0.1, kept as history
.claude/
  skills/               /feature, /fix, /round, /finish
  agents/               the pipeline's agents, one file each
  workflows/            diagnose.js (a fix's diagnosis), round.js (one round), final.js (the whole-feature check)
  hooks/                session brief, git guard, Ruff on edit, state reader, sync checker, test selector
  rules/                python.md (coding conventions, loaded for **/*.py) and structure-<subpackage>.md (signature detail)
.github/workflows/      CI: the gate and check_sync
.vscode/                editor config (Ruff as formatter, format on save)
pyproject.toml          packaging, Ruff, mypy and pytest configuration
DEVELOPMENT.md          development-side open questions and things to fix later (see CLAUDE.md)
DEVLOG.md               one entry per version
README.md               human guide, with the version: install, every source's endpoints and columns, the live Min Strøm and Eloverblik test commands, development setup
CLAUDE.md               routing map for Claude
STRUCTURE.md            this file
```

## Subpackage: `src/energydata/utils/`

Shared by the source subpackages; `httpx` and `pandas` are the runtime dependencies. Detail
(signatures and rules) lives in `.claude/rules/structure-utils.md`, loaded when working in
this subpackage. Modules: `src/energydata/utils/__init__.py` (re-exports every public name),
`src/energydata/utils/retry.py` (retrying sync/async request wrappers),
`src/energydata/utils/readers.py` (JSON/XML/CSV/ZIP body readers),
`src/energydata/utils/chunking.py` (date-range windows, chunked and concurrent chunked
fetch, ordered concurrent gather), `src/energydata/utils/api_client.py` (`ApiClient`, the source-agnostic client base
with its own event loop), `src/energydata/utils/periods.py` (`start`/`end` resolution),
`src/energydata/utils/zones.py` (bidding-zone normalisation) and
`src/energydata/utils/frames.py` (period index, block index, wide pivot, resolution expansion, NaN
padding, two-level column combining). Tests: `tests/test_retry.py`, `tests/test_readers.py`, `tests/test_chunking.py`,
`tests/test_api_client.py`, `tests/test_periods.py`, `tests/test_zones.py`,
`tests/test_frames.py`.

## Subpackage: `src/energydata/energidataservice/`

Energi Data Service endpoints on top of `utils`. Detail lives in
`.claude/rules/structure-energidataservice.md`, loaded when working in this subpackage.
Modules: `src/energydata/energidataservice/__init__.py` (re-exports the public names),
`src/energydata/energidataservice/client.py` (`EnergiDataServiceClient`),
`src/energydata/energidataservice/day_ahead.py` (`get_day_ahead_prices`),
`src/energydata/energidataservice/_markets.py` (private shared fetch-and-shape path),
`src/energydata/energidataservice/balancing.py` (imbalance, aFRR and mFRR energy prices),
`src/energydata/energidataservice/reserves.py` (mFRR/aFRR capacity, FCR-N, FCR-D, FCR DK1, FFR
prices), `src/energydata/energidataservice/dsos.py` (`Dso`, `DSOS`, the friendly-name DSO table) and
`src/energydata/energidataservice/pricelist.py` (DSO and Energinet tariffs and subscriptions, electricity
tax, from *DatahubPricelist*). Tests:
`tests/conftest.py` (the autouse no-network guard, the `markets_records` and `markets_service`
fixtures, the `pricelist_fixture` and `pricelist_service` fixtures and the `MarketsService` mock), `tests/fixtures/energidataservice_markets.json` (the
probe's real records for the seven market datasets), `tests/fixtures/energidataservice_pricelist.json`
(live price-list history for Energinet and six DSOs, plus a catalogue of every charge since 2025),
`tests/test_energidataservice_client.py`, `tests/test_day_ahead.py`, `tests/test_balancing.py`,
`tests/test_reserves.py`, `tests/test_dsos.py` and `tests/test_pricelist.py`.

## Subpackage: `src/energydata/minstroem/`

The Min Strøm Consumer API on top of `utils`; needs an API key (`MINSTROEM_API_KEY`). Detail
lives in `.claude/rules/structure-minstroem.md`, loaded when working in this subpackage.
Modules: `src/energydata/minstroem/__init__.py` (re-exports the public names),
`src/energydata/minstroem/client.py` (`MinStroemClient`, `MinStroemError`, `MissingApiKeyError`,
`BASE_URL`, `API_KEY_ENV`),
`src/energydata/minstroem/_series.py` (private shared path of the period endpoints),
`src/energydata/minstroem/addresses.py` (`get_addresses`),
`src/energydata/minstroem/usage.py` (`get_consumption`, `get_solar_export`),
`src/energydata/minstroem/costs.py` (`get_consumption_costs`, `get_solar_export_revenue`) and
`src/energydata/minstroem/prices.py` (`get_prices`, `get_forecast_prices`,
`get_prices_with_forecast`). Tests: `tests/conftest.py` (also the `MinStroemService` mock, the
`minstroem_address`, `minstroem_price_row` and `minstroem_request_id` helpers, the
`minstroem_service` and `live_report` fixtures, the live summary hook and
`block_real_transports`; its no-network guard lets `live` tests through),
`tests/minstroem_live_mock.py` (a pytest plugin, loaded with `-p`, that runs the live suite
offline against the mock in nine scenarios of data, refusing real transports from
`pytest_configure` on, with the clock pinnable by `MINSTROEM_LIVE_NOW`),
`tests/fixtures/minstroem_openapi.json` (the published OpenAPI spec),
`tests/test_minstroem_client.py`, `tests/test_minstroem_series.py`,
`tests/test_minstroem_addresses.py`, `tests/test_minstroem_usage.py`,
`tests/test_minstroem_costs.py`, `tests/test_minstroem_prices.py`, `tests/test_minstroem_spec.py`
(which also runs the live suite through that plugin) and `tests/test_minstroem_live.py` (marked
`live`, skipped without a key, deselected by plain `pytest`).

## Subpackage: `src/energydata/eloverblik/`

The Eloverblik Customer API on top of `utils`; needs a refresh token
(`ELOVERBLIK_REFRESH_TOKEN`). Detail lives in `.claude/rules/structure-eloverblik.md`, loaded
when working in this subpackage. Modules: `src/energydata/eloverblik/__init__.py` (re-exports
the public names), `src/energydata/eloverblik/client.py` (`EloverblikClient`, `EloverblikError`,
`MissingTokenError`, `BASE_URL`, `REFRESH_TOKEN_ENV`, `DEFAULT_POLICY`),
`src/energydata/eloverblik/_common.py` (private shared path of the point-scoped functions),
`src/energydata/eloverblik/meteringpoints.py` (`get_metering_points`,
`get_metering_point_details`), `src/energydata/eloverblik/meterdata.py` (`Aggregation`,
`AGGREGATIONS`, `get_meter_data`) and `src/energydata/eloverblik/charges.py` (`get_charges`).
Tests: `tests/conftest.py` (also the `EloverblikService` mock, its builders and the
`eloverblik_service` fixture), `tests/fixtures/eloverblik_openapi.json` (the published OpenAPI
spec), `tests/test_eloverblik_live.py` (marked `live`, skipped without a token, deselected by
plain `pytest`, runnable offline with `ELOVERBLIK_LIVE_MOCK=1`), and the offline suites
`tests/test_eloverblik_client.py`, `tests/test_eloverblik_common.py`,
`tests/test_eloverblik_meteringpoints.py`, `tests/test_eloverblik_meterdata.py`,
`tests/test_eloverblik_charges.py` and `tests/test_eloverblik_spec.py`. The `ApiClient.request`
headers/json, `ApiClient.request_response` and `gather_ordered` changes are covered in `tests/test_api_client.py` and
`tests/test_chunking.py`.

## Package: `src/energydata/`

### `src/energydata/__init__.py`

Package entry point. Re-export the public API here so callers can
`from energydata import X` rather than reaching into modules, using relative imports so
it survives renaming the package folder. Nothing is exported yet — the placeholder script
has no public API.

Every package directory under `src/`, including every subpackage added later, needs one of
these. A directory of modules without it is not a package, so it will not install.

### `src/energydata/hello_world.py`

Placeholder so the package is not empty. Delete the whole file when real code arrives.

| Signature | Description |
|---|---|
| `main() -> None` | Print `Hello, World!`. |

It is deliberately trivial and is **not** the conventions reference.

Runnable standalone: `python -m energydata.hello_world`, once the package is installed
(`pip install -e ".[dev]"`). Under a `src/` layout the repo root is not on `sys.path`, so
without the install it fails with `No module named energydata` — an un-set-up
environment, not a broken module.

## Tests: `tests/`

All tests live here and nowhere else: `testpaths = ["tests"]` in `pyproject.toml` means
`pytest` collects nothing outside this directory. The suites of the four source subpackages
are named in their sections above. The `live` suites (`tests/test_minstroem_live.py`,
`tests/test_eloverblik_live.py`) are deselected by `addopts` (`-m 'not live'`) and need
credentials.

| File | Covers |
|---|---|
| `tests/test_hello_world.py` | the placeholder's `main` via `capsys`; deleted with the script |
| `tests/test_dev_state.py` | parsing, malformed files, active-brief choice, git helpers; its `write_brief`/`write_round` helpers are reused by other tests |
| `tests/test_session_brief.py` | the brief text and `main`'s output |
| `tests/test_check_sync.py` | every check, including the version raise against a throwaway repo |
| `tests/test_guard_git.py` | the guard, vendored with it |
| `tests/test_select_tests.py` | every selection rule on a throwaway repo (transitive and relative imports, re-exporting `__init__`, helpers, deletions, renames, cycles, unparseable files), `changed_files` and the CLI |

## Hooks: `.claude/hooks/`

Stdlib only; importable by each other and by the tests (`pythonpath` in `pyproject.toml`).

### `.claude/hooks/dev_state.py`

Reads `brief.json` and `round.json` leniently: a malformed file reads as None.

| Signature | Returns |
|---|---|
| `RoundEntry` | Frozen dataclass: `number`, `name`, `slug`, `branch`, `status`, `summary`; property `folder -> str` (`r<n>-<slug>`). |
| `Brief` | Frozen dataclass: `path`, `feature`, `title`, `branch`, `bump`, `status`, `summary`, `current_round`, `rounds: tuple[RoundEntry, ...]`, `pr`, `kind` (`feature` or `fix`); properties `folder -> Path`, `live -> bool`; `round_entry(number: int) -> RoundEntry \| None`. |
| `RoundState` | Frozen dataclass: `path`, `round`, `name`, `branch`, `status`, `step`, `steps: tuple[str, ...]`, `attempt`, `question`, `note`; property `step_name -> str`. |
| `read_brief(path: Path) -> Brief \| None` | One `brief.json`, or None if unusable. |
| `read_round(path: Path) -> RoundState \| None` | One `round.json`, or None if unusable. |
| `all_briefs(project_dir: Path) -> list[Brief]` | Every brief under `development/`, newest first. |
| `active_brief(project_dir: Path, branch: str = "") -> Brief \| None` | The live brief, preferring the checked-out feature or round branch. |
| `round_state(brief: Brief, number: int) -> RoundState \| None` | That round's `round.json`. |
| `read_diagnosis(path: Path) -> RoundState \| None` | A fix's diagnosis `state.json`, read as a `RoundState` with `round` 0 and the diagnosis steps. |
| `diagnosis_state(brief: Brief) -> RoundState \| None` | The feature's `diagnosis/state.json`, or None. |
| `git_lines(project_dir: Path, args: list[str]) -> list[str]` | Non-empty output lines of a git command, `[]` on failure. |
| `current_branch(project_dir: Path) -> str` | The checked-out branch, or `""`. |
| `main() -> None` | Prints the live feature and its current round. |

Constants: `DEV_DIR`, `DIAGNOSIS_DIR`, `KINDS`, `LIVE_STATUSES`, `FEATURE_STATUSES`,
`ROUND_STATUSES`, `STEP_STATUSES`, `ROUND_STEPS` (the nine step names), `DIAGNOSIS_STEPS` (the
four diagnosis steps).

### `.claude/hooks/session_brief.py`

`SessionStart` hook: injects the live feature's state and the skill that resumes it.

| Signature | Returns |
|---|---|
| `brief(project_dir: Path, feature: Brief) -> str` | The paragraph for the session's context. |
| `main() -> None` | Emits it as `additionalContext`, or nothing when no feature is live. |

### `.claude/hooks/check_sync.py`

Checks the documents agree with each other and with the code. Run by `/finish` and CI.

| Signature | Returns |
|---|---|
| `parse_version(text: str) -> tuple[int, int, int] \| None` | The version, or None if not exactly `X.Y.Z`. |
| `bumped(version: tuple[int, int, int], bump: str) -> tuple[int, int, int]` | The version raised by `major`/`minor`/`patch`; raises `ValueError` otherwise. |
| `project_version(pyproject_text: str) -> str` | `[project] version`, or `""`. |
| `version_problems(project_dir: Path) -> list[str]` | pyproject, README `**Version:**` and newest DEVLOG entry disagree; DEVLOG order. |
| `structure_problems(project_dir: Path) -> list[str]` | Python files missing from STRUCTURE.md, or named but absent. |
| `state_problems(project_dir: Path) -> list[str]` | Unparseable state files, unknown statuses. |
| `branch_brief(project_dir: Path, branch: str) -> Brief \| None` | The brief whose feature or round branch is `branch`. |
| `bump_problems(project_dir: Path, base: str) -> list[str]` | Version not raised above `base`'s by this branch's brief's bump (any raise without one). |
| `problems(project_dir: Path, base: str = "") -> list[str]` | All of the above. |
| `main() -> None` | CLI: `[--base REF] [--root DIR]`; prints problems, exits 1 if any. |

### `.claude/hooks/select_tests.py`

Picks the test files a change can affect, so a round runs those and CI runs the whole suite.
The changes are `git diff <base>...HEAD` plus uncommitted and untracked files. The full suite
(None) for `pyproject.toml`, a `conftest.py`, a non-test file under the test directory, a
re-exporting `__init__.py`, an unparseable `.py` file or a file it cannot place; otherwise
the changed test files, `test_<module>.py` (and `test_<pkg>_<module>.py`) of each changed
module, and every test file that depends on a changed module transitively, read with `ast`
(imports, `import_module("...")`, and strings naming a module or its path). Prose,
`development/` and `.claude/` outside `hooks/` select nothing; other configuration selects
the tests that name it. Module roots come from `pythonpath` and `testpaths`;
`[tool.select_tests] ignore` adds path prefixes that select nothing.

| Signature | Returns |
|---|---|
| `Selection` | Frozen dataclass: `tests: tuple[str, ...] \| None` (None = full suite), `reason`, `total`; `summary() -> str` (`selected 4 of 61: ...`, `full suite: ...`, `no tests apply: ...`). |
| `changed_files(project_dir: Path, base: str) -> list[str]` | Changed paths against `base...HEAD`, committed or not, sorted; renames as both paths. Raises `ValueError` when git fails. |
| `selection(project_dir: Path, changed: Iterable[str]) -> Selection` | The selection with its reason. |
| `select_tests(project_dir: Path, changed: Iterable[str]) -> list[str] \| None` | The test paths, `[]` when none applies, None for the full suite. |
| `main() -> None` | CLI: `[--base REF] [--root DIR]`; one test per line, `ALL`, or nothing on stdout, the reason on stderr; `ALL` when the changes cannot be listed. |

### `.claude/hooks/guard_git.py`

`PreToolUse` hook on `Bash` and `PowerShell`: refuses a `git commit` or `git push` that would
land on `main`, reading the command as a shell would (quoting, separators, subshells, branch
switches chained with `&&`). Vendored from the template.

| Signature | Returns |
|---|---|
| `Segment` | Frozen dataclass: `tokens`, `separator`. |
| `segments(command: str) -> list[Segment] \| None` | The command split into invocations, or None if unreadable. |
| `git_subcommand(tokens: tuple[str, ...]) -> tuple[str, tuple[str, ...]]` | The git subcommand and its arguments. |
| `push_targets_main(args: tuple[str, ...], branch: str) -> bool` | Whether a push would update `main`. |
| `switch_target(subcommand: str, args: tuple[str, ...]) -> str` | The branch a checkout/switch moves to, `""`, or `UNRESOLVED`. |
| `violation(command: str, branch: str) -> str` | The reason to refuse, or `""`. |
| `main() -> None` | Entry point: allow or refuse. |

### `.claude/hooks/lint_py.py`

`PostToolUse` hook: `ruff format` and `ruff check --fix` on an edited `.py` file.

| Signature | Returns |
|---|---|
| `find_ruff(project_dir: Path) -> Path \| None` | Ruff in the project venv. |
| `run(ruff: Path, args: list[str]) -> CompletedProcess[str]` | Ruff's result. |
| `target_file(payload: dict[str, object], project_dir: Path) -> Path \| None` | The edited `.py` file. |
| `main() -> None` | Format, fix, report. |

## Pipeline configuration: `.claude/`

| Path | Role |
|---|---|
| `skills/feature/` | `/feature`: Main Conceptualize (Opus medium), background `repo-scout`s, BRIEF, starts round 1 |
| `skills/fix/` | `/fix`: intake, runs `workflows/diagnose.js`, agrees cause, scope and bump with the user, then the rounds with `kind: "fix"` |
| `skills/round/` | `/round`: coordinates one round (Sonnet high), runs `workflows/round.js`, answers or escalates questions |
| `skills/finish/` | `/finish`: runs `workflows/final.js`, extras, `docs-writer`, version, pull request, then CI through to green |
| `workflows/diagnose.js` | A fix's four diagnosis steps; args `feature_dir`, `branch`, `symptom`, `start_step`, `answers`; returns `diagnosed` (with `is_bug` and the `defect`), `needs_input` or `failed` |
| `workflows/round.js` | The nine round steps (step 7 runs the tests `select_tests.py` picks against the feature branch, `--ff` plus the earlier failures in the fix loop); args `feature_dir`, `round_dir`, `round`, `feature_branch`, `round_branch`, `start_step`, `attempt`, `kind`, `answers` (`kind` `fix` adds the fix-round rules); returns `merged`, `needs_input` or `failed` |
| `workflows/final.js` | Read-only whole-feature check: test gate first (the selection against `origin/main`, not the full suite), then three independent concept checks and two extras scouts; returns `tests`, `checks`, `extras`, `missing` |
| `agents/reproducer.md`, `cause-hunter.md`, `root-cause-analyst.md`, `diagnosis-critic.md` | The diagnosis's four steps (`/fix`) |
| `agents/repo-scout.md` | Read-only investigation for `/feature`, one angle each |
| `agents/round-conceptualizer.md`, `round-planner.md` | Round steps 1 and 2 (Opus) |
| `agents/implementer.md` | Step 3, and fix mode for failing gates |
| `agents/verifier.md`, `code-reviewer.md` | Step 4 |
| `agents/test-designer.md`, `test-writer.md`, `test-runner.md` | Steps 5 to 7 |
| `agents/concept-checker.md` | Step 8 and the final check |
| `agents/round-closer.md` | Step 9: merge into the feature branch |
| `agents/extras-scout.md`, `docs-writer.md` | `/finish` |
| `rules/python.md` | Conventions for `**/*.py` |
| `rules/structure-utils.md`, `structure-energidataservice.md`, `structure-minstroem.md`, `structure-eloverblik.md` | Per-subpackage signature detail split out of this file (see Growth), each loaded by its `paths` frontmatter when Claude works in that subpackage or its tests |
| `settings.json` | Hooks and pre-approved commands |
