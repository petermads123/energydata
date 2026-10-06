# Energi Data Service tariffs, subscriptions and elafgift

<!-- claude-plan step=8 status=active -->

| Field | Value |
|---|---|
| Feature | `feat/energidataservice-tariffs` |
| Round | `1` |
| Branch | `feat/energidataservice-tariffs` |
| Started | `2026-10-06` |

## Progress

| # | Step | Skill | Runs | Status |
|---|---|---|---|---|
| 1 | Conceptualize | `/conceptualize` | with the user | done |
| 2 | Plan | `/plan` | with the user | done |
| 3 | Implement | `/implement` | in `/build` | done |
| 4 | Verify | `/verify` | in `/build` | done |
| 5 | Test | `/test` | in `/build` | done |
| 6 | Concept check | `/concept-check` | in `/build` | done |
| 7 | Ship | `/ship` | in `/build` | done |
| 8 | Recommend | `/recommend` | with the user | pending |
| 9 | Pull request | `/create-pr` | with the user | pending |
| 10 | Review | `/watch-pr` | on the pull request | pending |

Statuses: `pending`, `in progress`, `done`.

## Builds on

Nothing — this is the first round. The branch is cut from `feat/energidataservice-markets` (PR #4, open), so it carries the market endpoints and `version = "1.0.0"`; neither may break.

---

## 1. Concept

### What this is

Five public functions in `energydata.energidataservice` that read Energi Data Service's
*DatahubPricelist* (subscriptions, tariffs and fees; filter column `ValidFrom`; data from
2014) and return the C-customer grid charges and the electricity tax named in
`ideas/energydata/energidataservice-datasets.md`. All values are DKK, excl. VAT.

| Function | Returns | Source rows |
|---|---|---|
| `get_dso_tariffs(dso, start, end=None)` | the DSO's standard C-customer consumption tariff, one row per hour | `ChargeType = D03`, the DSO's GLN and its mapped C tariff code |
| `get_energinet_tariffs(start, end=None)` | `system_tariff` and `transmission_tariff`, one row per hour | GLN `5790000432752`, codes `41000` (Systemtarif) and `40000` (Transmissions nettarif) |
| `get_dso_subscriptions(dso, start, end=None)` | the DSO's standard C consumption subscription, one row per validity period | `ChargeType = D01`, the DSO's mapped C subscription code |
| `get_energinet_subscriptions(start, end=None)` | the TSO system subscription, one row per validity period | GLN `5790000432752`, code `41004` |
| `get_electricity_tax(start, end=None)` | `electricity_tax`, the normal rate, one row per hour | GLN `5790000432752`, code `EA-001` |

What the data looks like (live API, 2026-10-06):
- A price-list row has a validity period, `ValidFrom` and `ValidTo` (local Danish dates),
  and `Price1`–`Price24` in DKK/kWh. `Price1` covers 00:00–01:00 local.
- `ResolutionDuration` is `PT1H` (24 hourly prices), `P1D` (one price per day, in
  `Price1`) or `P1M` (a monthly subscription, in `Price1`, DKK/month).
- Energinet's tariffs and the tax are `P1D`:
  - 2026: system 0.072, transmission 0.043, elafgift 0.008 DKK/kWh.
  - Elafgift was 0.72 in 2025.
- The TSO subscription is 15.58 DKK/month in 2026.
- About 36 DSOs publish a C-customer tariff, each under its own codes:
  - Radius `DT_C_01`, Cerius `30TR_C_ET`, N1-131 `CD`.
  - Several DSOs publish more than one C variant: plain, "time", Flex, below/above
    100 MWh, and discount rows. Which code is "the standard C consumption tariff" and
    "subscription" is therefore a curated mapping, built from the data in step 2 and shown
    to the user before the build.

### Why it is worth building

These are the remaining cost components a consumer in Denmark pays per kWh on top of the
spot price: the DSO tariff, Energinet's tariffs and the electricity tax. With them, the
`heatingsystem` repo can compute the full variable price of an hour. It joins on the
`start`/`end` columns against the 15-minute day-ahead prices.

### Inputs and outputs

- **Inputs:** `dso` (a friendly name, for the DSO functions), `start` and an optional `end`
  (the same rules as the market functions, at hourly resolution), and an optional `client`.
- **Hourly outputs** (tariffs, tax):
  - A long `pd.DataFrame` with tz-aware `Europe/Copenhagen` `start` and `end` columns, one
    half-open row per hour of `[start, end)` in order (23 or 25 rows on DST days), plus the
    value column(s) as float DKK/kWh.
  - Each hour takes the row valid on its local date. A `PT1H` row contributes the price for
    its local wall-clock hour, so the repeated autumn hour gets that hour's price twice; a
    `P1D` row contributes the same price to every hour.
- **Subscription outputs:**
  - One row per validity period overlapping `[start, end)`, with `start` and `end`
    clipped to the request, and a `subscription` column in DKK/month.
  - A stretch with no row is a row with NaN.

### How it connects to the rest of the repo

It uses `EnergiDataServiceClient` and the period helpers (`resolve_period`,
`period_index`), and, where they fit, the private market path or its pieces. It touches
none of the existing functions. Branch: `feat/energidataservice-tariffs`, cut from
`feat/energidataservice-markets` (open as PR #4), so the markets code is available.

### Explicitly out of scope

- Discount rows ("Rabat …").
- Every tariff and subscription variant other than the standard C consumption one: "time"
  (hourly metered) where a plain one exists, Flex, above 100 MWh, production/feed-in
  (indfødning), availability (rådighed), and A/B-customer tariffs.
- One-off fees (`D02`).
- The reduced (electric-heating) elafgift. It is not published in *DatahubPricelist*.
- VAT.
- Energinet's other charges: Nettab, Balancetarif, Effektabonnement, and the
  large-consumer system tariff.

Assumptions:

- `ValidTo` is exclusive (a row valid 2026-01-01 → 2027-01-01 covers all of 2026), and a
  missing `ValidTo` means "until further notice". Step 2 confirms both against the data.
- Where two rows of the same code overlap in time, the one with the later `ValidFrom`
  wins. Step 2 checks whether this happens.
- Friendly DSO names are lowercase, built from the company name, with the grid-area
  number where one company runs several areas: `n1-131`, `n1-344`, `konstant-151`.

### Acceptance criteria

| # | The finished feature... |
|---|---|
| A1 | `get_dso_tariffs(dso, start, end=None)` returns a long DataFrame with tz-aware `Europe/Copenhagen` `start`/`end` columns, one half-open row per hour of `[start, end)` in order, and a float `tariff` column in DKK/kWh excl. VAT. Each hour takes the price-list row valid on its local date: a `PT1H` row's `Price{n}` applies to local hour n−1, and a `P1D` row's price applies to every hour. DST days give 23 or 25 rows, with the repeated autumn hour priced as its wall-clock hour. The `start`/`end` rules are the same as the market functions, at hourly resolution. |
| A2 | `dso` is a friendly name from a public mapping, also listed in the README. It resolves to the DSO's GLN and its standard C tariff and C subscription codes. Every DSO in the price list that publishes a C-customer tariff is supported. An unknown name raises `ValueError` listing the valid names. |
| A3 | `get_energinet_tariffs(start, end=None)` has the same hourly shape, with `system_tariff` (code 41000) and `transmission_tariff` (code 40000) columns. |
| A4 | `get_dso_subscriptions(dso, start, end=None)` and `get_energinet_subscriptions(start, end=None)` (code 41004) return one row per validity period overlapping `[start, end)`. Each row's `start`/`end` is clipped to the request, and a `subscription` column holds DKK/month. |
| A5 | `get_electricity_tax(start, end=None)` has the same hourly shape, with an `electricity_tax` column in DKK/kWh at the normal rate (code EA-001). Its docstring says the reduced electric-heating rate is not published in this dataset. |
| A6 | The full requested period is always returned. An hour with no valid price row is NaN; a stretch with no subscription row is a row with NaN. |
| A7 | Every call goes through `EnergiDataServiceClient` and works inside a running event loop. Tests run on real price-list records committed as a fixture, without the network. The README rows and docstrings give the dataset, charge codes, currency, unit, resolution, format and the supported DSOs. |

### Open questions

None.

---

## 2. Plan

### Approach

**One fetch, one row picker, two shapers.** Every function in this round does the same
three things with *DatahubPricelist*:

1. Fetch the rows for one GLN and a short list of charge codes, filtered server-side on
   `GLN_Number`, `ChargeType` and `ChargeTypeCode`. The filter is verified live: Radius's
   whole C-tariff history since 2017 is 35 rows, and Energinet's four codes since 2019 are
   32.
2. For each moment, pick the row valid then: `ValidFrom <= t < ValidTo` (a null `ValidTo`
   means open-ended), with the latest `ValidFrom` winning on overlap.
3. Shape the result:
   - **hourly:** one row per hour, `PriceN` for local hour N−1, or `Price1` for a `P1D` row;
   - **periods:** one row per validity stretch, for the monthly subscriptions.

All of it lives in one new module, `energidataservice/pricelist.py`. The DSO table lives in
`energidataservice/dsos.py`, as data.

Because `ValidFrom` is the API's filter column, a `start` filter would drop a row that began
before the period and is still valid. So the fetch asks for everything from the dataset's
first date (2014-01-01) up to the period's end, in **one** request.

**Observed filter semantics (live, 2026-10-06).** The API filters `ValidFrom` by date. Both
`start` and `end` are cut to their date part, `end` is exclusive, and `timezone` makes no
difference:
- `start=2026-01-01T00:00, end=2026-01-01T01:00` returns nothing for a row with `ValidFrom`
  2026-01-01;
- `start=2026-01-01, end=2026-01-02` returns that row.

The client sends UTC timestamps, so the fetch's end bound is set to **local midnight two days
after the period's last local date**. This can never cut off a row that starts on the
period's last day; the row picker discards the extra rows. A period ending on or before
2014-01-01 makes no request and returns the all-NaN frame.

Records come back with `ValidFrom`/`ValidTo` as naive local Danish midnights
(`2026-01-01T00:00:00`), whatever `timezone` is. That is cheap because the
filter cuts it to tens of rows. To do this, `EnergiDataServiceClient.fetch_dataset` and
`get_dataset` gain one optional keyword, `max_span: timedelta | None = None`. It overrides the
client's window size for that call. The change is additive: existing calls are unchanged.

Rejected:

- **Reusing `_markets._get`.** It pivots one value per timestamp and zone. The price list
  needs validity rows expanded into hours, which is a different shape, and forcing it in
  would bend both.
- **Matching DSO rows by note text at run time** (for example "Nettarif C"). The data shows
  several DSOs with two or more C codes, some named "time" or "Flex", rebates whose notes
  contain "Nettarif C", and codes renamed between years (Konstant, 2026). A curated table is
  explicit, testable, and wrong in a visible way when a DSO changes a code.
- **Paging the 12-year span in 31-day windows.** That would be about 150 requests per call
  for tens of rows.

### Modules

| Path | New or changed | Purpose |
|---|---|---|
| `src/energydata/energidataservice/dsos.py` | new | `Dso` dataclass and `DSOS`, the friendly-name table below. |
| `src/energydata/energidataservice/pricelist.py` | new | The five functions plus private fetch, row-picking and shaping helpers. |
| `src/energydata/energidataservice/client.py` | changed | `fetch_dataset`/`get_dataset` gain keyword `max_span: timedelta \| None = None`. |
| `src/energydata/energidataservice/__init__.py` | changed | Re-exports `Dso`, `DSOS` and the five functions. |
| `tests/fixtures/energidataservice_pricelist.json` | new | Fetched live during the build. It has two parts: full-history rows for Energinet's four codes and for six DSOs (Radius, Cerius, Konstant-151, N1-131, Elinord, Hurup); and a *catalogue* of every distinct (GLN, ChargeOwner, ChargeType, ChargeTypeCode, Note) since 2025 for the completeness test. No prices are in the catalogue. |
| `tests/conftest.py` | changed | `MarketsService` gains a per-dataset filter-field mapping. `DatahubPricelist` filters on `ValidFrom`, comparing its date against the date part of the `start`/`end` strings (end exclusive), as the API does; the existing datasets keep their time fields. A `pricelist_service` fixture wraps it with the pricelist records. There is no second mock. |
| `tests/test_pricelist.py`, `tests/test_dsos.py` | new | One suite per new module. `tests/test_energidataservice_client.py` is extended for `max_span`. |
| `README.md` | changed | Five endpoint rows and the DSO name table. |
| `STRUCTURE.md`, `.claude/rules/structure-energidataservice.md` | changed | New modules (full paths), signatures, test files and the fixture. |
| `DEVELOPMENT.md` | changed | Removes the "Tariffs, subscriptions and elafgift are next" entry, which this branch resolves, and adds the pre-2025 code note (Risks). |

### The DSO table (for the user to check)

These codes were chosen from the live price list (2025–2027 rows). Where a DSO publishes
two C codes with identical prices, the first is taken. A DSO's code list is fetched together.
On overlap the latest `ValidFrom` wins; on an equal `ValidFrom`, the earlier code in the
list wins, then the row whose note has no "Flex"/"time". The data has no equal-`ValidFrom`
pairs within any mapped code (checked).

Same-code notes vary over time: `5NCFF`, `TNT15000`, `AAL-NT-05`, `NT-C`, `151-NT01T` and
`E-51` sometimes say "Flex" or "time". The prices are the code's own, so the label is not
filtered on. Midtfyns' `TNT15000` (sometimes labelled Flex) and `TNT15001` (sometimes
labelled Time) carry identical prices in every month of 2025–2026, and so do `AB15000` and
`AB15001` in 2026.

| Name | DSO (ChargeOwner) | GLN | C tariff code(s) | C subscription code(s) |
|---|---|---|---|---|
| `aal` | Aal El-Net A M B A | 5790001095451 | `AAL-NT-05` | `AAL-E-50` |
| `cerius` | Cerius A/S | 5790000705184 | `30TR_C_ET` | `30AB_CT` |
| `dinel` | Dinel A/S | 5790000610099 | `TCL<100_02` | `ACL<100_01` |
| `elektrus` | Elektrus A/S | 5790000836239 | `6000091` | `6000082` |
| `elinord` | Elinord A/S | 5790001095277 | `43300` (daily price) | `41300` |
| `elnet-midt` | Elnet Midt A/S | 5790001100520 | `T3001` | `20001` |
| `elvaerk` | Netselskabet Elværk A/S | 5790000681358 | `5NCFF` | `5ACFF` |
| `flow` | FLOW Elnet A/S | 5790000392551 | `FE1 NT-01` | `FE1 E-50` |
| `forsyning-elnet` | Forsyning Elnet A/S | 5790001088309 | `STR-NT-03` (daily) | `STR-E-50` |
| `grindsted` | Grindsted Elnet A/S | 5790000681105 | `GEV-NT-01` (daily) | `GEV-E-50` |
| `hammel` | Hammel Elforsyning Net A/S | 5790001090166 | `C-Tarif` | `55000` |
| `hjerting` | Hjerting Transformatorforening | 5790001095376 | `C-Tarif` | `HE-E-50` |
| `hurup` | Hurup Elværk Net A/S | 5790000610839 | `HEV-NT-01T` ("Nettarif fra 0-100000 kWh") | `HEV-E-50` ("Abonnement") |
| `ikast` | Ikast El Net A/S | 5790000682102 | `IEV-NT-01` (daily) | `IEV-E-50` |
| `kimbrer` | Kimbrer Elnet A/S | 5790001095239 | `C-Tarif` | `AARS-E-50` ("Net abo forbrug time") |
| `konstant-151` | Konstant Net A/S - 151 | 5790000704842 | `151-NT01T`, `C_FBTNTR_B` (code changed 2026) | `151-E5004`, `C_FBAHM__B` |
| `konstant-245` | Konstant Net A/S - 245 | 5790000683345 | `245-NT01T`, `C_FBTNTR_B` | `245-E5004`, `C_FBAHM__B` |
| `l-net` | L-Net A/S | 5790001090111 | `3000` | `4100` |
| `laesoe` | Læsø Elnet A/S | 5790001103460 | `43100` (daily) | `41100` |
| `midtfyns` | Midtfyns Elforsyning A.m.b.A | 5790001089023 | `TNT15000` | `AB15000` |
| `n1-016` | N1 A/S - 016 | 5790002502699 | `C-Tarif` | `K_22000` |
| `n1-131` | N1 A/S - 131 | 5790001089030 | `CD` | `CD` |
| `n1-344` | N1 A/S - 344 | 5790000611003 | `T-C-F-F-TD` | `A-C-F-04` |
| `noe` | NOE Net A/S | 5790000395620 | `30030` | `32310` |
| `nord-energi` | Nord Energi Net A/S | 5790000610877 | `TAC` | `ABC` |
| `radius` | Radius Elnet A/S | 5790000705689 | `DT_C_01` | `DA_C_F_01` |
| `rah` | RAH Net A/S | 5790000681327 | `RAH-C` | `ABON-COPG` |
| `ravdex` | Ravdex A/S | 5790000836727 | `NT-C` | `E-50C1` |
| `sunds` | Sunds Net A.m.b.a | 5790001095444 | `SEF-NT-05` | — (publishes no C consumption subscription) |
| `tarm` | Tarm Elværk Net A/S | 5790000706419 | `TEV-NT-01T` | `TEV-E-50` |
| `trefor` | TREFOR El-net A/S | 5790000392261 | `C` | `E-51` |
| `trefor-oest` | TREFOR El-net Øst A/S | 5790000706686 | `46` | `E-50` |
| `veksel` | Veksel A/S | 5790001088217 | `NT-01` | `E-50` |
| `vores-elnet` | Vores Elnet A/S | 5790000610976 | `TNT1009` | `AB1012` (50 DKK/month, service line owned by the DSO; `AB1013` is 48, owned by the customer) |
| `zeanet` | Zeanet A/S | 5790001089375 | `43110` | `41100` |

That is 35 DSOs. Left out:

- Energinet (`5790000432752`) has its own functions.
- `Sanity` (`5799994000107`) is a test owner.
- Two unnamed GLNs had rows only in 2025 (`5790000610822`, codes `VE-…`; `5790001088231`,
  code `94TR_C_ET`): DSOs since merged.

### Public API

| Signature | Module | Purpose | Covers |
|---|---|---|---|
| `Dso` | `energidataservice.dsos` | A frozen dataclass with fields: `name: str`, `owner: str`, `gln: str`, `tariff_codes: tuple[str, ...]`, `subscription_codes: tuple[str, ...]` (empty when the DSO publishes none). | A2 |
| `DSOS: Mapping[str, Dso]` | `energidataservice.dsos` | The table above, keyed by friendly name and sorted. It is a read-only mapping (`MappingProxyType`). | A2 |
| `get_dso_tariffs(dso: str, start: TimeLike, end: TimeLike \| None = None, *, client: EnergiDataServiceClient \| None = None) -> pd.DataFrame` | `energidataservice.pricelist` | Hourly long frame with columns `start`, `end` and `tariff` (DKK/kWh excl. VAT), from the DSO's C tariff codes. `dso` is matched case-insensitively; an unknown name raises `ValueError` listing `sorted(DSOS)`. | A1, A2, A6, A7 |
| `get_energinet_tariffs(start: TimeLike, end: TimeLike \| None = None, *, client: EnergiDataServiceClient \| None = None) -> pd.DataFrame` | `energidataservice.pricelist` | Hourly long frame with columns `start`, `end`, `system_tariff` (41000) and `transmission_tariff` (40000), from one request. | A3, A6, A7 |
| `get_dso_subscriptions(dso: str, start: TimeLike, end: TimeLike \| None = None, *, client: EnergiDataServiceClient \| None = None) -> pd.DataFrame` | `energidataservice.pricelist` | Period rows with columns `start`, `end` and `subscription` (DKK/month). A DSO with no subscription code gives one all-NaN row covering the period, and no request is made. | A2, A4, A6, A7 |
| `get_energinet_subscriptions(start: TimeLike, end: TimeLike \| None = None, *, client: EnergiDataServiceClient \| None = None) -> pd.DataFrame` | `energidataservice.pricelist` | Period rows from code 41004. | A4, A6, A7 |
| `get_electricity_tax(start: TimeLike, end: TimeLike \| None = None, *, client: EnergiDataServiceClient \| None = None) -> pd.DataFrame` | `energidataservice.pricelist` | Hourly long frame with columns `start`, `end` and `electricity_tax` (EA-001, normal rate, DKK/kWh). The docstring says the reduced electric-heating rate is not published in *DatahubPricelist*. | A5, A6, A7 |
| `EnergiDataServiceClient.fetch_dataset(..., sort_by: str \| None = None, max_span: timedelta \| None = None)` and `get_dataset(...)` (same addition) | `energidataservice.client` | Gains `max_span`: when given, it overrides the client's window size for this call, under the same validation (positive, whole minutes). | A7 |
| `main() -> None` in `dsos.py` and `pricelist.py` | — | Showcases. `dsos` is offline (prints the table); `pricelist` is live. | — |

Shared behaviour:
- **`start`/`end`:** these follow `resolve_period(..., resolution=1h)`: naive input is
  Danish time, a lone date is the whole day, a lone timestamp is one hour, `end` is
  exclusive, and bad input raises `ValueError` naming the value.
- **Columns:** `start` and `end` are `datetime64[ns, Europe/Copenhagen]`, and the index is a
  plain `RangeIndex`.
- **Client:** a client passed in is left open; one the function creates is closed.
- **Docstrings:** each has a property table (dataset, GLN and codes, currency and unit,
  resolution, format, and supported DSOs by reference to `DSOS`) and `Raises:`.

### Implementation guide

1. **`client.py`: the `max_span` keyword.** Thread it through `fetch_dataset` and
   `get_dataset` into the window split, validated like the constructor's.
2. **`dsos.py`.** Write `Dso` and `DSOS` from the table above, exactly. The showcase prints
   the names and GLNs.
3. **`pricelist.py`, private helpers:**
   - `_fetch(client, gln, codes, charge_type, last)`: calls `fetch_dataset("DatahubPricelist", origin, fetch_end, filters={"GLN_Number":[gln], "ChargeType":[charge_type], "ChargeTypeCode":list(codes)}, columns=[...the needed fields...], sort_by="ValidFrom", max_span=fetch_end - origin)`. Here `origin` is 2014-01-01 local and `fetch_end` is local midnight two days after `last`'s local date (see the observed semantics above). When `last <= origin`, it skips the request.
   - **Charge types:** the DSO tariff and Energinet 41000, 40000 and EA-001 are `D03`; the
     DSO subscription and Energinet 41004 are `D01`.
   - A row whose `ResolutionDuration` is not `PT1H`/`P1D` (tariffs) or `P1M`
     (subscriptions) raises `EnergiDataServiceError` naming the value and the code.
   - `_rows(records)`: parses `ValidFrom`/`ValidTo` as local dates (`Europe/Copenhagen`
     midnight), with `ValidTo` null meaning +∞.
   - `_hourly(rows, index)`: for each slot, picks the active row (latest `ValidFrom` among
     those valid at that instant). The value is `Price{local hour + 1}` for `PT1H` rows and
     `Price1` for `P1D` rows; no active row, or a `None` price, gives NaN. Implement it
     vectorised per row: mask the slots in `[from, to)`, then apply later rows over
     earlier ones.
   - `_periods(rows, first, last)`: cuts `[first, last)` at every row boundary, takes the
     active row per piece, merges adjacent pieces with the same active row, and emits one
     row per piece (NaN where nothing is active).
4. **`pricelist.py`, the public functions.**
   - Resolve the period, then `period_index(first, last, 1h)` for the hourly ones.
   - Use the owned-client pattern from `day_ahead.py`.
   - Energinet's tariffs fetch codes 41000 and 40000 together, then split by code.
5. **`__init__.py` re-exports; README rows and the DSO table; `STRUCTURE.md` and the rules
   file (full paths); `DEVELOPMENT.md` edits.**
6. **Fixture**, fetched live during the build (the API is reachable):
   - **Rows:** filter by GLN and code for Energinet (41000, 40000, 41004, EA-001), and for
     radius, cerius, konstant-151, n1-131, elinord and hurup with their mapped codes, from
     2014.
   - **Catalogue:** distinct GLN/owner/type/code/note since 2025, each with its latest
     `ValidTo` (null = open), built from a `columns=`-trimmed query.
   - Save both to `tests/fixtures/energidataservice_pricelist.json` as a JSON object
     `{"records": [...], "catalogue": [...]}`, in the same shape the markets fixture uses.
7. **Tests**, per the intents below.
8. **Live check.** Run the `pricelist` showcase and spot-check that Radius 2026-10-15 18:00
   equals that row's `Price19`, and that Energinet 2026 gives system 0.072 and transmission
   0.043.

### Test intents

| # | Must prove | Covers |
|---|---|---|
| T1 | `DSOS` completeness against the catalogue. Every GLN whose C consumption tariff is still valid (latest `ValidTo` null or after 2026-10-06), except the two merged 2025-only GLNs listed by name, (a note matching `Nettarif C`/`C-Kunde` that is not a discount, production, feed-in, availability or regional row, plus Hurup's 0-100000 kWh tariff) is in `DSOS`. Every mapped code exists under its GLN with the right `ChargeType`. Names are unique, lowercase and kebab-case. | A2 |
| T2 | `get_dso_tariffs` on fixture rows. Radius picks `Price{h+1}` per local hour across a season switch (e.g. 2026-03-31 → 2026-04-01). Elinord's daily price fills all hours. Konstant-151 uses `151-NT01T` in 2025 and `C_FBTNTR_B` after the code change. DST days give 23/25 rows, with the repeated 02:00 hour priced with `Price3` twice. A lone date gives 24 rows and a lone timestamp one. A lone timestamp at 00:00 on a row's `ValidFrom` day returns the new row's price (the fetch's end padding); so does an explicit period ending at 01:00 that day. An hour before the first row is NaN, and a period before 2014-01-01 is all NaN with no request. | A1, A6 |
| T3 | Overlap and edges. When two rows overlap, the latest `ValidFrom` wins. `ValidTo` is exclusive (the row's last day ends at midnight). A null `ValidTo` is open-ended. A `None` price gives NaN. An equal-`ValidFrom` tie resolves by code order. An unknown `ResolutionDuration` raises `EnergiDataServiceError` naming it. The output columns are exactly `start, end, tariff`, with `end - start == 1h` on every row, including the DST hours in elapsed time. | A1, A6 |
| T4 | `get_energinet_tariffs` returns 2026 hours with 0.072/0.043 and 2025 hours with 0.074/0.061. One request carries both codes. | A3 |
| T5 | Subscriptions. Radius over 2026 gives rows split at its validity boundaries, the first and last clipped to the request, in DKK/month. A gap before the first row is a NaN row. Energinet 41004 gives 15.166666 for 2025 and 15.583333 for 2026. `sunds` gives one all-NaN row and no request. | A4, A6 |
| T6 | `get_electricity_tax`: 2025 hours 0.72, 2026 hours 0.008, and a mid-2023 row change (0.008 → 0.697 on 2023-07-01) lands on the right hour. The docstring states the reduced rate is not published. | A5 |
| T7 | Requests and the client. The exact filter (GLN, `ChargeType` `D03`/`D01` per function, codes) is sent, with start 2014-01-01 and end two local days past the period, `sort=ValidFrom asc` and the needed `columns`, as **one** request even for a 10-year period. `max_span` overrides the window and is validated. An unknown DSO (`"radius "`, `"xyz"`) raises `ValueError` listing names, with no request made. Case-insensitive names work (`"Radius"`). An owned client is closed (also on HTTP 400); a passed one is left open; calls work inside a running event loop. | A2, A7 |
| T8 | README rows and docstrings name the dataset, codes, DKK/kWh or DKK/month, resolution, format and the DSO table. The no-network guard holds. | A7 |

### Coverage check

- **Every criterion has an API entry:** A1 `get_dso_tariffs`; A2 `Dso`, `DSOS` and the DSO
  functions; A3 `get_energinet_tariffs`; A4 the two subscription functions; A5
  `get_electricity_tax`; A6 all five; A7 all five, plus `max_span`.
- **Every criterion has a test intent:** A1 T2, T3; A2 T1, T7; A3 T4; A4 T5; A5 T6; A6 T2,
  T3, T5, T6; A7 T7, T8.
- **Nothing in the Public API is without a criterion:** `max_span` traces to A7 (one request
  per call through the client), and the showcases follow the module convention.

### Critique

Read by the `plan-critic` (verdict: accept with changes). Settled with live probes where the
finding was about data:

1. **The fetch's end bound could drop the row starting on the last day.** Applied, and
   proven live: the API filters `ValidFrom` by date and ignores `timezone`, so the fetch now
   ends two local days past the period (Approach, guide 3, T2).
2. **The record form and the mock's comparison rule were unstated.** Applied: records are
   naive local midnights, and the mock compares dates as the API does (Approach, Modules).
3. **The tie-break was undefined.** Applied: on overlap the latest `ValidFrom` wins, then
   code order, then no Flex/time. The data has no ties within mapped codes; same-code
   Flex/time labels carry the code's own prices (DSO-table preamble, T3).
4. **midtfyns appeared to be on the Flex lineage.** Rebutted with data: `TNT15000` and
   `TNT15001` have identical prices every month of 2025–2026 and only their labels swap, so
   the choice does not change a price. Raised to the user at the gate anyway.
5. **T1 needed "current", but the catalogue had no validity.** Applied: the catalogue
   carries the latest `ValidTo`, and the merged GLNs are explicit exclusions.
6. **A period before 2014 was unhandled.** Applied: it makes no request and returns all
   NaN (guide 3, T2).
7. **Charge types per code, and unknown resolutions, were unstated.** Applied: D03 for the
   tariffs and EA-001, D01 for the subscriptions; an unknown resolution raises
   `EnergiDataServiceError` (guide 3, T3, T7).
8. **The plan reimplemented `MarketsService`.** Applied: it gains a per-dataset filter field
   instead of a second mock (Modules).
9. **Wording.** Applied: the fixture is a JSON object like the markets fixture. The DSO
   count (35, with the merged GLNs excluded) is stated at the gate.

### Risks

- **History older than the mapped codes.** The table is built from 2025–2027 codes. A DSO
  that used a different code before 2025 returns NaN for those years. Radius `DT_C_01`
  reaches back to 2017, so it is not affected. Add a `DEVELOPMENT.md` note; this is not a
  halt.
- **A DSO changes a code in future.** Its new rows are not fetched and the hours go NaN,
  visibly. This is covered by the same note; the T1 catalogue test catches it when the
  fixture is refreshed.
- **`max_span` across the whole history.** A 12-year single request must stay under the
  API's response limits. Tens of rows is far below them; if a real call ever truncates,
  `fetch_dataset`'s `total` check raises `EnergiDataServiceError` rather than returning
  partial data.
- **The fixture fetch hits the rate limit (HTTP 429).** The client honours the holdoff, so
  the fetch is slow but succeeds. If it still fails after one retry, halt (a gate failing
  twice).
- **A Hurup, Kimbrer or Vores Elnet choice turns out to be wrong.** These are user-visible
  table rows decided at this step. Changing them is a one-line data change, not a halt.
- **Anything that would change section 1** halts the build.

---

## 3. Implementation notes

Deviations from the plan (none changes the Public API table or an acceptance criterion):

- The row tie-break "then the row whose note has no Flex/time" is not implemented: `Note` is
  not fetched, since the plan records no equal-`ValidFrom` pair within any mapped code. Ties
  resolve by `ValidFrom`, then code order.
- `_fetch` became `_load` and uses the sync `get_dataset` (which goes through `run`); the
  owned-client pattern is as in `day_ahead.py`.
- `client.py` gained a private `_check_span`, shared by the constructor and the `max_span`
  override.

Left for step 5 (done there): the fixture fetch (`tests/fixtures/energidataservice_pricelist.json`), the
`conftest.py` `MarketsService` filter-field mapping and `pricelist_service` fixture, and all
tests (`test_pricelist.py`, `test_dsos.py`, `max_span` in `test_energidataservice_client.py`).
`STRUCTURE.md` and the rules file name the new tests only through the rules file's `paths`.

Live check (2026-10-06, one showcase run plus a few raw queries): Radius 2026-10-15 18:00
is 0.955573, equal to that row's `Price19` (ValidFrom 2026-10-01); Energinet 2026-06-01 gives
system 0.072 and transmission 0.043; elafgift 2025-12-31 is 0.72 and 2026-01-01 0.008; the
Radius subscription over 2025-12-01 to 2026-02-01 is one row of 36.773011. Every mapped
tariff code since 2025 has `ResolutionDuration` `PT1H` (429 rows) or `P1D` (20), and every
mapped subscription code `P1M` (104), so no row raises.

---

## 4. Verification log

| Check | Result |
|---|---|
| `ruff check .` | All checks passed! |
| `ruff format --check .` | 72 files already formatted |
| `mypy` | Success: no issues found in 39 source files |
| `pytest` | 1484 passed |
| Plan completeness | every signature in the Public API table exists as written (`Dso`, `DSOS`, five pricelist functions, `max_span` on `fetch_dataset`/`get_dataset`, both `main`); no Missing, Deviation or Unplanned |
| `STRUCTURE.md` | auditor: in sync for step 3. Applied the leftover-"and" wording fix; the test-file entries (`test_pricelist.py`, `test_dsos.py`, fixture, `pricelist_service`) are step 5's, since the stop gate counts an entry for a missing file as drift |
| `python -m energydata.energidataservice.dsos` | offline; prints 35 DSOs with GLNs and owners (expected RuntimeWarning) |
| `python -m energydata.energidataservice.pricelist` | not re-run (rate limit); step 3's live run stands. Showcase is in the required form; fixed the comment saying "whole year" for a three-hour period |

---

## 5. Test log

Suite: 1735 passed (1484 before this step); `ruff check`, `ruff format --check` and `mypy` clean. The fixture was fetched live (14 requests, one per GLN, charge type and code list, plus the catalogue query) into `tests/fixtures/energidataservice_pricelist.json` as `{"records", "catalogue"}`; the catalogue is the 118k raw D01/D03 rows reduced to 2579 distinct (GLN, owner, type, code, note) valid since 2025, each with `LatestValidTo`. A mutation check (end padding one day, `ValidTo` inclusive, tie sign flipped, hour off by one, `last < _ORIGIN`) fails the suite each time.

| Intent | Test names | Result |
|---|---|---|
| T1 | `test_dsos_cover_every_gln_with_a_current_c_consumption_tariff`, `test_every_dso_gln_is_in_the_catalogue_with_a_current_tariff`, `test_every_mapped_code_exists_under_its_gln_and_charge_type`, `test_catalogue_owners_match_the_dso_owner_names`, `test_dsos_keys_are_sorted_lowercase_kebab_and_equal_the_name`, `test_dsos_matches_the_plan_table` (35 rows), `test_dsos_holds_exactly_the_plan_names`, `test_dsos_glns_are_thirteen_digits_and_unique_and_never_energinets`, `test_dsos_codes_are_tuples_of_non_empty_strings`, `test_only_sunds_has_no_subscription_code`, `test_dsos_is_read_only_and_dso_is_frozen`, `test_dsos_codes_may_be_shared_between_dsos_but_not_within_one_gln` | pass |
| T2 | `test_get_dso_tariffs_hour_n_takes_price_n_plus_one`, `..._radius_evening_peak_is_the_live_checked_value`, `..._prices_every_hour_by_its_wall_clock_hour` (23/25/24 rows), `..._autumn_repeats_hour_two_with_price_three_twice`, `..._season_switch_changes_rows_at_local_midnight`, `..._summer_midnight_on_a_valid_from_day_takes_the_new_row` (lone timestamp and period ending at 01:00), `..._daily_row_fills_every_hour`, `..._follows_the_konstant_code_change`, `..._hour_before_the_first_row_is_nan_and_the_period_is_full`, `..._before_2014_is_all_nan_and_makes_no_request`, `..._touching_2014_makes_one_request`, `..._follows_the_start_and_end_rules` | pass |
| T3 | `..._latest_valid_from_wins_and_the_older_row_resumes`, `..._valid_to_is_exclusive`, `..._valid_from_is_inclusive`, `..._open_ended_row_covers_the_far_future`, `..._a_none_price_is_nan_and_hides_the_older_row`, `..._a_zero_price_is_zero_not_nan`, `..._equal_valid_from_is_won_by_the_earlier_code` (both response orders), `..._later_valid_from_beats_the_earlier_code`, `..._ignores_the_order_the_service_returns_rows_in`, `test_hourly_functions_reject_an_unsupported_resolution_naming_it_and_the_code`, `test_get_dso_tariffs_frame_has_exactly_the_documented_shape` | pass |
| T4 | `test_get_energinet_tariffs_keeps_system_and_transmission_apart`, `..._is_one_request_with_both_codes`, `..._columns_are_exact`, `..._on_the_autumn_day_has_25_constant_rows`, `..._changes_value_at_the_year_boundary`, `..._with_one_code_missing_gives_nan_for_it_only`, `..._with_no_rows_at_all_is_all_nan` | pass |
| T5 | `test_get_energinet_subscriptions_splits_at_the_year_and_clips_the_ends`, `..._a_lone_date_is_one_day_on_the_boundary`, `..._a_lone_timestamp_is_one_hour`, `test_get_dso_subscriptions_one_row_spans_a_request_inside_one_validity`, `..._two_rows_with_the_same_price_stay_two_rows`, `..._a_gap_before_the_first_row_is_a_nan_row`, `..._cover_the_request_without_gaps_or_overlaps`, `..._frame_has_the_documented_dtypes`, `..._sunds_is_one_nan_row_and_no_request`, `test_energinet_subscriptions_before_2014_is_one_nan_row_and_no_request`, `test_subscriptions_an_inner_bounded_row_splits_the_open_row_in_three`, `..._a_zero_length_row_does_not_split_the_period`, `..._a_none_price_is_a_nan_row`, `..._period_edge_on_a_row_boundary_does_not_use_the_old_row`, `test_subscriptions_reject_a_resolution_other_than_monthly` | pass |
| T6 | `test_get_electricity_tax_changes_on_the_right_local_hour` (2025/26 cut, 2023-07-01 summer cut, lone timestamps in winter and summer, 2022), `test_get_electricity_tax_columns_and_request_are_exact`, `test_the_tax_docstring_says_the_reduced_rate_is_not_published` | pass |
| T7 | `test_the_fetch_runs_from_2014_to_two_local_days_after_the_last_date` (winter, summer, midnight-exclusive), `test_the_fetch_asks_for_exactly_the_columns_it_reads`, `test_each_function_sends_exactly_its_filter`, `test_get_dso_subscriptions_konstant_requests_d01_and_both_codes`, `test_n1_131_shares_code_cd_and_only_the_charge_type_tells_tariff_from_subscription`, `test_codes_with_special_characters_round_trip_through_the_filter`, `test_a_ten_year_period_is_still_one_request_on_a_31_day_client`, `test_a_long_period_costs_one_request_per_function`, `test_the_max_span_the_functions_use_covers_the_whole_history`; in `test_energidataservice_client.py`: `test_max_span_overrides_the_client_window_for_one_call`, `..._none_keeps_the_client_window`, `..._can_be_smaller_than_the_client_window`, `..._does_not_change_the_client_window_afterwards`, `..._equal_to_the_period_is_one_window_across_a_dst_change`, `..._of_one_minute_is_valid`, `..._that_is_not_positive_whole_minutes_raises_before_any_request` (4), `..._error_names_the_offending_span`, `test_fetch_dataset_max_span_works_on_the_client_loop`; unknown DSO: `test_unknown_dso_raises_listing_every_name_and_makes_no_request` (8 names x 2 functions), `test_a_dso_name_that_is_not_text_raises_value_error`, `test_unknown_dso_with_an_owned_client_never_creates_one`, `test_dso_name_is_case_insensitive`; client: `test_an_owned_client_is_created_used_and_closed` (5 functions), `..._closed_when_the_service_refuses`, `..._closed_when_a_row_is_malformed`, `test_a_passed_client_is_left_open` (5), `test_calling_twice_on_one_client_gives_equal_frames`, `test_a_closed_passed_client_raises_runtime_error_when_a_request_is_needed` (5), `test_a_closed_passed_client_goes_unnoticed_when_no_request_is_needed`, `test_the_functions_work_inside_a_running_event_loop` | pass |
| T8 | `test_each_docstring_names_dataset_codes_unit_resolution_and_format` (5), `test_each_docstring_has_args_returns_and_raises` (5), `test_the_dso_docstrings_say_the_name_is_case_insensitive`, `test_the_subscription_docstring_names_sunds_and_the_missing_request`, `test_the_package_reexports_the_public_names`, `test_readme_lists_every_dso_with_its_gln_and_codes`, `test_readme_names_the_five_functions_dataset_and_units`, `test_a_real_client_cannot_reach_the_network_in_tests`, `test_main_prints_each_showcase_against_a_stub`, `test_main_prints_every_name_and_gln` | pass |

Bugs the tests and designers found, and what was done (all fixed in `pricelist.py`; no public signature changed):

- An empty or `"NaT"` `ValidFrom`/`ValidTo` parsed to `NaT`, so a row silently covered nothing (or, for `ValidTo`, vanished). `_local` now raises `EnergiDataServiceError`; tests `test_a_malformed_row_raises_instead_of_being_priced` (12 cases).
- `_rows` matched on code only, so a service ignoring the filter could mix DSOs that share a code (`C-Tarif`, `41100`, `E-50`, `CD` as tariff and subscription). The fetch now also reads `GLN_Number` and `ChargeType`, and `_rows(records, gln, charge_type, codes, resolutions)` refuses a record of another GLN or type. The request's `columns` gained those two fields (the plan said "the needed fields"). Tests: `test_tariffs_reject_a_record_of_another_gln_that_shares_the_code`, `..._a_subscription_record_that_shares_the_code`, `test_subscriptions_reject_a_tariff_record_that_shares_the_code`.
- `_dso(None)` and other non-text names raised `AttributeError`; now `ValueError` naming the value (`test_a_dso_name_that_is_not_text_raises_value_error`).
- A zoned `ValidFrom`/`ValidTo` (`"2026-01-01T00:00:00Z"`) was converted but not snapped to local midnight, so it started at 01:00 local; it now counts as its Danish local date (`test_a_zoned_valid_from_counts_as_its_local_date`). The API sends naive dates, so this is defensive.

Wrong expectations corrected in the tests: Midtfyns' `ChargeOwner` is spelled with a double space in the price list, so the owner check compares whitespace-normalised (the `owner` field is informational and the plan table was approved with one space); and a design-brief expectation that the request's `start` is `2014-01-01` is `2013-12-31T23:00` on the wire (local midnight in UTC), asserted as such.

Contradictions from the designers, applied or rebutted:

1. Empty/`NaT` validity strings: applied, above.
2. Aware validity dates not normalised: applied, above.
3. Matching on code only: applied, above.
4. `_dso(non-str)`: applied, above.
5. Flex/time note tie-break not implemented: already a recorded deviation (section 3); no test written; step 6 to read it as a plan-text versus code difference.
6. Closed passed client unnoticed when no request is made (`sunds`, a period before 2014): documented rather than changed. Every public docstring's `Raises:` now says `RuntimeError` only "if a request is needed", the rules file says so, and `test_a_closed_passed_client_goes_unnoticed_when_no_request_is_needed` pins it.
7. Konstant tie-break favours the retired code: rebutted. The data has no equal-`ValidFrom` pair within any mapped code (checked at step 2 and again on the fixture: `151-NT01T` and `C_FBTNTR_B` overlap in March 2026 with different `ValidFrom`s, so the later wins, and the two carry equal prices there); the tie is a latent case only and its resolution by code order is pinned (`test_get_dso_tariffs_equal_valid_from_is_won_by_the_earlier_code`). Not worth a `DEVELOPMENT.md` entry.
8. `start` is `2013-12-31T23:00` on the wire: wording only, see above.
9. "One row per validity period" versus identical prices: checked against the fixture. Radius' 2025-12-01 to 2026-02-01 is one row (its row runs 2024-12-01 to 2026-04-01), so the live check stands; and Radius 2023 and 2024-01..07 hold two rows of 44.75, which stay two rows, pinned by `test_get_dso_subscriptions_two_rows_with_the_same_price_stay_two_rows`.
10. Two `EnergiDataServiceError` branches not named in `Raises:`: `_load`'s `Raises:` now names the foreign record and the bad validity dates; the public docstrings keep "unexpected payload".

Edge cases considered and deliberately skipped, with reasons:

- Very long strings and non-ASCII beyond the DSO names: nothing branches on length; `læsø` and the special-character codes (`TCL<100_02`, `FE1 NT-01`) are covered.
- Mutation of arguments: every argument is immutable; `DSOS` read-only-ness and the mock's data staying untouched are covered.
- Float precision beyond equality: prices go through `float()`; compared exactly where the fixture value is a literal and with `pytest.approx` for the 6-digit subscription prices.
- Konstant-245 and the other 29 DSOs' history: the fixture holds six DSOs on purpose; the other DSOs share the code path and are covered by the plan-table and catalogue tests.
- The Flex/time note tie-break: not implemented (recorded deviation), so untestable.
- `Note`-based filtering and the live API's server-side behaviour: the mock reproduces the observed date-cut semantics; the live check is step 3's.

## 6. Concept check

Run 2026-10-06 against live data (Radius, Energinet, elafgift, six DSOs outside the fixture) and the suite (1735 passed; ruff and mypy clean; the markets, day-ahead and client suites are inside that run).

| # | Criterion | Met | Evidence |
|---|---|---|---|
| A1 | hourly `tariff`, tz-aware, DST, price-by-hour | yes | Live: `get_dso_tariffs("Radius","2026-10-15")` gives 24 rows, `datetime64[ns, Europe/Copenhagen]` start/end, float `tariff`, 18:00 = 0.955573 (= `Price19`); 2026-10-25 gives 25 rows, 2026-03-29 gives 23. Tests `test_get_dso_tariffs_hour_n_takes_price_n_plus_one`, `..._autumn_repeats_hour_two_with_price_three_twice`, `..._prices_every_hour_by_its_wall_clock_hour`. |
| A2 | friendly names, README table, all DSOs, `ValueError` | yes | `DSOS` has 35 entries (`dsos.py`), README table at README.md:~75-110 checked by `test_readme_lists_every_dso_with_its_gln_and_codes`; completeness against the catalogue by `test_dsos_cover_every_gln_with_a_current_c_consumption_tariff`; live `get_dso_tariffs("xyz", ...)` raises `ValueError` listing names; live 2026-10-15 for n1-344, midtfyns, vores-elnet, konstant-151, laesoe, hurup (none in the fixture except hurup) all give 0 NaN hours and a subscription. |
| A3 | Energinet `system_tariff`/`transmission_tariff` | yes | Live 2025-12-31T23:00 to 2026-01-01T01:00: transmission 0.061 then 0.043 (system likewise changes); `test_get_energinet_tariffs_keeps_system_and_transmission_apart`, `..._is_one_request_with_both_codes`. |
| A4 | subscriptions per validity period, clipped | yes | Live Energinet 2025-06-01 to 2026-03-01: rows 15.166666 (to 2026-01-01) and 15.583333, ends clipped to the request; Radius 2025-12-01 to 2026-02-01: one row 36.773011. Tests `..._splits_at_the_year_and_clips_the_ends`, `..._cover_the_request_without_gaps_or_overlaps`. |
| A5 | `electricity_tax`, docstring on reduced rate | yes | Live: 0.72 at 2025-12-31 23:00, 0.008 at 2026-01-01 00:00; docstring says the reduced rate is not published (`test_the_tax_docstring_says_the_reduced_rate_is_not_published`). |
| A6 | full period, NaN for gaps | yes | Live Radius 2013-12-31 to 2014-01-02: 48 rows, all NaN; live `sunds` subscription: one NaN row spanning the request. Tests `..._hour_before_the_first_row_is_nan_and_the_period_is_full`, `..._a_gap_before_the_first_row_is_a_nan_row`. |
| A7 | via client, loop-safe, fixture tests, README/docstrings | yes | `_load` uses `EnergiDataServiceClient.get_dataset` (`pricelist.py`); suite runs offline with the autouse guard (`test_a_real_client_cannot_reach_the_network_in_tests`); every docstring carries the property table (`test_each_docstring_names_dataset_codes_unit_resolution_and_format`); fixture `tests/fixtures/energidataservice_pricelist.json`. |

Drift found, and what was done about it:

- **Recorded deviation, no Flex/time note tie-break (section 3).** Section 1 does not mention a tie-break at all (only "the later `ValidFrom` wins"), so no criterion is affected. The plan's own text said ties are resolved by code order then note; the data has no equal-`ValidFrom` pair within any mapped code, and the live spot-checks above are unaffected. Accepted as a plan-text versus code difference; the plan text is left as the historical record.
- **Structure auditor.** Both items applied to `.claude/rules/structure-energidataservice.md` (the `pricelist.py` validity/price refusals; the `test_dsos.py` description).
- **Out of scope:** nothing from the exclusion list was built (no discount, variant, one-off, reduced-rate or VAT code). **Surface:** only the five functions, `Dso`, `DSOS` and the additive `max_span` keyword; the markets functions are untouched.
- **Showcase:** `python -m energydata.energidataservice.pricelist` equivalents run live above read as a worked example (named inputs, one call, a result).

### Earlier rounds still hold

| Round | # | Criterion | Still met | Evidence |
|---|---|---|---|---|
| | | none: first round (the markets branch's suites `test_balancing.py`, `test_reserves.py`, `test_day_ahead.py`, `test_energidataservice_client.py` pass in the 1735) | n/a | |

---

## 7. Ship log

| Field | Value |
|---|---|
| Commits | cf9f5d2 WIP step 2: plan written, awaiting plan-critic; 065c408 Concept; 6c6bcac Plan; 944d1da Plan accepted; c5aac70 WIP step 3: max_span, dsos, pricelist; 333c92f Add DatahubPricelist tariffs, subscriptions and elafgift; c51d0eb Verify; c5d2e7d WIP step 5: pricelist fixture, mock date filter, GLN/ChargeType guard; 135865f Test; 2a190c9 Concept check; plus the Ship commit. Every step left a commit. Whole tree on 2026-10-06: ruff, ruff format, mypy clean; pytest 1735 passed. No stray files. Reviewed against origin/feat/energidataservice-markets (this round's own changes only). |
| Pushed to | `origin/feat/energidataservice-tariffs` |

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

