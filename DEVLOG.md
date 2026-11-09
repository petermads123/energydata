# Devlog

One entry per version, newest first: what changed, then the files it touched. `/finish`
writes each entry in the commit that opens the pull request.

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
