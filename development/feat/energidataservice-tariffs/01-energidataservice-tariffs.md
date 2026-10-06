# Energi Data Service tariffs, subscriptions and elafgift

<!-- claude-plan step=3 status=active -->

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

