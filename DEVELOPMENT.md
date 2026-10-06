# Development notes

Development-side open questions and things to fix later: what the code does not do yet, a
question nobody can answer yet, an idea that fell short of a recommendation. Not a design
document and not a changelog.

**Adding an entry:** a heading, the date and the branch/round it came from, the item itself,
and its next step or where it is configured. **Resolving an entry:** the change that
resolves it deletes it — this file only ever shows what is still open. Step 8 of every round
ends by cleaning it: entries the branch resolved are removed, duplicates are merged, and
only open items are left. Before an entry is deleted or renamed, anything that points at it
by heading is updated in the same change.

## `feat/shared-http-utils` round 1 (2026-10-06)

- **Date windows are elapsed-time, not local-midnight.** `date_windows` steps in UTC, so a
  Copenhagen-midnight start with a whole-day span lands boundaries at 23:00 local after the
  October change. Eloverblik takes dates, so its subpackage must normalise boundaries to
  dates itself (or pick a span with margin).
- **Pass the CSV delimiter explicitly for Danish data.** `read_csv`'s sniffer can pick `,`
  on a one-column file with decimal commas (`41,5`) and then reports ragged rows. The Energi
  Data Service client reads JSON, so this applies to any source or dataset read as CSV:
  pass `delimiter=";"` rather than rely on sniffing.
- **Flat `tests/` will collide.** One `test_<module>.py` per module in a flat `tests/` breaks
  once two subpackages have a module of the same name (e.g. two `client.py`); decide on
  `tests/<subpackage>/` with `__init__.py` files before the second source subpackage lands.
  The first (`energidataservice`) sidestepped it with `test_energidataservice_client.py`.
- **`read_json` accepts `NaN` and `Infinity`.** Stdlib leniency, pinned by a test; revisit
  if a source ever returns them and a caller needs them refused.

## `feat/energidataservice-client` round 1 (2026-10-06)

- **`_gather_ordered` is imported across modules.** `energidataservice/day_ahead.py` uses the
  private helper from `utils/chunking.py` for the lowest-index re-raise rule; make it public
  (with a `structure-utils.md` row) when a second caller needs it.

## `feat/energidataservice-markets` round 1 (2026-10-06)

- **Hourly capacity functions drop off-grid rows.** mFRR/aFRR capacity, FCR-N/D and FFR are
  hourly; a `:15`/`:30`/`:45` record is silently dropped (pinned by a test). If Energinet
  moves a capacity dataset to a 15-minute MTU, the function would return a quarter of the
  data without error — revisit the resolution then.
- **FCR DK1 on an autumn DST day is unverified live.** Synthetic records prove the
  00/04/…/20 block grid; the live check of 2025-10-26 hit the rate limit.
- **README and docstring wording has no test.** Plan intent T8 was skipped at step 5; step 6
  read all ten docstrings and README rows instead. A test like `test_day_ahead`'s
  docstring/README check would pin them.

## `feat/energidataservice-tariffs` round 1 (2026-10-06)

- **Pre-2025 DSO codes are unmapped.** `DSOS` was built from 2025-2027 price-list rows. A DSO
  that used a different tariff or subscription code before 2025 returns NaN for those years
  (Radius `DT_C_01` reaches back to 2017 and is unaffected). Likewise a DSO that changes a
  code in future goes NaN visibly until `dsos.py` is updated; refreshing the fixture's
  catalogue and the completeness test catches it.
