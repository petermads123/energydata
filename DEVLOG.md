# Devlog

One entry per version, newest first: what changed, then the files it touched. `/finish`
writes each entry in the commit that opens the pull request.

## 2.0.3 — 2026-11-20

Test runs now report their progress live, as in template 0.2.4. A small pytest plugin,
`pytest_progress.py`, loaded for every run by `-p pytest_progress` in `pyproject.toml`'s `addopts`, keeps
`.pytest_cache/progress.json` current while tests run: how many were collected, how many are
done, passed, failed and skipped, the test running now, and whether the run has finished. The
`feature-band` mod (0.3.0) reads it and draws a bar above the round and step bars, so step 7
of a round and the final check show where they are. Nothing in the agents or workflows
changes: any pytest run in the repo writes the file, it is ignored by git, and a file that
cannot be written never fails a run.

Files:
- `.claude/hooks/pytest_progress.py`, `tests/test_pytest_progress.py`
- `CLAUDE.md`, `STRUCTURE.md`, `README.md`, `DEVLOG.md`, `pyproject.toml`

## 2.0.2 — 2026-11-09

The concept conversation asks one question at a time, as in template 0.2.1. In `/feature`
every turn that waits for the user ends on exactly one question, never a list, and the four
repo scouts no longer write into the chat: a report that arrives while a question is open is
noted in `BRIEF.md` under `## Scout findings`, the turn ends without a message, and the
findings shape the next question once the user answers. The rounds are proposed only after
every report is in. `/fix` asks its intake and its agreement the same way.

Files:
- `.claude/skills/feature/SKILL.md`, `.claude/skills/fix/SKILL.md`
- `README.md`, `DEVLOG.md`, `pyproject.toml`

## 2.0.1 — 2026-11-09

The Claude workflow moves from the ten-step plan-file pipeline to the round-based pipeline of
`petermads123/template` (0.2.0). A feature is now `/feature` (concept, branch, bump, repo scouts,
a BRIEF with rounds), each round runs as the `round` workflow (conceptualize, plan, implement,
verify, test design, tests, the tests the change can affect, concept check, merge), a bug starts
with `/fix` and its diagnosis, and `/finish` runs the whole-feature check, writes the version
and the documents and opens the pull request. State lives in `brief.json` and `round.json`
files under `development/`, read by `dev_state.py` and `session_brief.py`; `check_sync.py`
keeps the version, `README.md`, `DEVLOG.md`, `STRUCTURE.md` and the code in agreement, and
`select_tests.py` picks the tests a round must run. CI (`.github/workflows/check.yml`) runs the
gate and `check_sync.py` on every pull request; the live suites stay deselected, so it needs no
API keys.

Removed: the fourteen old skills, the agents `brainstormer`, `plan-critic` and
`structure-auditor`, `plan_state.py` and `stop_gate.py` with their tests, and
`development/TEMPLATE.md`. The numbered plan files under `development/feat/` stay as history.
Kept as they were: the four `.claude/rules/structure-*.md` files, the `live` marker and the
`-m 'not live'` default in `pyproject.toml`, and `DEVELOPMENT.md`'s open entries. The test that
asked the stop gate for this subpackage's STRUCTURE.md problems now asks `check_sync`. No
package code changed.

Files:
- `.claude/skills/`, `.claude/agents/`, `.claude/workflows/`, `.claude/hooks/`, `.claude/settings.json`, `.claude/rules/`
- `.github/workflows/check.yml`, `.gitignore`, `development/README.md`
- `tests/test_dev_state.py`, `test_session_brief.py`, `test_check_sync.py`, `test_select_tests.py`, `test_guard_git.py`, `test_minstroem_spec.py`
- `CLAUDE.md`, `STRUCTURE.md`, `README.md`, `DEVLOG.md`, `DEVELOPMENT.md`, `pyproject.toml`
