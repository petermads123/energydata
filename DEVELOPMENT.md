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
  on a one-column file with decimal commas (`41,5`) and then reports ragged rows; the
  Energi Data Service subpackage should pass `delimiter=";"` rather than rely on sniffing.
- **Flat `tests/` will collide.** One `test_<module>.py` per module in a flat `tests/` breaks
  once two subpackages have a module of the same name (e.g. two `client.py`); decide on
  `tests/<subpackage>/` with `__init__.py` files before the second source subpackage lands.
- **`read_json` accepts `NaN` and `Infinity`.** Stdlib leniency, pinned by a test; revisit
  if a source ever returns them and a caller needs them refused.
