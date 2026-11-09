# energydata

APIs for collecting energy data from four sources: Energinet's
[Energi Data Service](https://www.energidataservice.dk/), the
[Min Strøm](https://docs.minstroem.app/introduction) Consumer API,
[Eloverblik](https://eloverblik.dk/) and the
[ENTSO-E Transparency Platform](https://transparency.entsoe.eu/).

**Version:** 2.0.1

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

### Min Strøm

The [Min Strøm Consumer API](https://docs.minstroem.app/introduction) serves a household's own
metered data and its electricity prices, including an **hourly price forecast**. It needs a
**Min Strøm Plus** subscription and an API key created in the Min Strøm iOS app. Pass the key as
`api_key=` to `MinStroemClient`, or set the `MINSTROEM_API_KEY` environment variable; everything
imports and constructs without one, and the first request raises `MissingApiKeyError`. The key
never appears in an exception message or a `repr`. The API allows 60 requests a minute and 2,000
a day; the client retries a 429 after its `Retry-After`. Only the Consumer API is covered, not
the Enterprise API.

```python
from energydata.minstroem import get_forecast_prices, get_prices_with_forecast

forecast = get_forecast_prices()  # the account's only address
print(forecast.attrs["currency"], forecast.attrs["fetched_at"])

# Confirmed prices where they exist, forecast after the last one.
prices = get_prices_with_forecast("2026-10-26")
```

Every function returns a `pandas.DataFrame` indexed by interval start (tz-aware
`Europe/Copenhagen`, named `time`), one row per interval of `[start, end)` on the Danish
calendar (23 or 25 hours on a DST day). The API returns sparse rows: an interval it has no row
for is **NaN** (`<NA>` for the boolean columns), never zero. Currency (`DKK`, `SEK` or `NOK`),
whether VAT is included and, for the forecast, the fetch time are in `frame.attrs`. Every
address-scoped function takes a keyword-only `address_id` (a UUID); left out, it uses the
account's only address and raises `ValueError` for none or several (listing them). A period
longer than an endpoint's cap is split on Copenhagen calendar boundaries and joined again. A
bound that is not on the interval (for example `aggregation="month"` starting mid-month) is a
`ValueError`, never silently widened. An API error raises `MinStroemError` carrying `status`,
`error_code`, `reason` and `request_id`.

| Function | Unit | Interval | Columns | Endpoint |
|---|---|---|---|---|
| `get_forecast_prices(*, address_id=None)` | currency/kWh incl. VAT | 1 hour, no period: the rows the API returns | `total_price_per_kwh`, `variable_charges_per_kwh`; `attrs` `fetched_at` | `GET /me/addresses/{id}/forecast-prices` (regions DK1, DK2, SE3, SE4) |
| `get_prices_with_forecast(start, end=None, *, address_id=None)` | currency/kWh incl. VAT | 1 hour | the two price columns and `is_forecast`; confirmed wins, forecast only after the last confirmed hour | `prices` and `forecast-prices` |
| `get_prices(start, end=None, *, resolution="hour", address_id=None)` | currency/kWh incl. VAT | `hour`, `fifteenMinutes` | `total_price_per_kwh`, `variable_charges_per_kwh` | `GET /me/addresses/{id}/prices` (366 days hourly, 90 days quarter-hourly per request) |
| `get_consumption(start, end=None, *, aggregation="hour", address_id=None)` | kWh | `hour`, `day`, `month`, `year` | `energy_kwh`, `is_estimate` | `GET /me/addresses/{id}/consumption` |
| `get_solar_export(start, end=None, *, aggregation="hour", address_id=None)` | kWh | `hour`, `day`, `month`, `year` | `energy_kwh`, `is_estimate` | `GET /me/addresses/{id}/solar-export` |
| `get_consumption_costs(start, end=None, *, aggregation="hour", address_id=None)` | currency, incl. VAT | `hour`, `day`, `month`, `year` | `energy_kwh`, `total_price_per_kwh`, `fixed_fees`, `total_cost`, `is_estimate` | `GET /me/addresses/{id}/consumption-costs` |
| `get_solar_export_revenue(start, end=None, *, aggregation="hour", address_id=None)` | currency, excl. VAT | `hour`, `day`, `month`, `year` | `energy_kwh`, `total_price_per_kwh`, `fixed_fees`, `net_revenue`, `is_estimate` | `GET /me/addresses/{id}/solar-export-revenue` |
| `get_addresses()` | | one row per address | `id`, `label`, `formatted_address`, `region`, `time_zone`, `grid_operator_name`, `supplier_name`, and the six capability flags | `GET /me/addresses` |

Plain `pytest` never touches the network and deselects the tests marked `live`. To run the live
tests against the real API, from the repo root in PowerShell (the key goes inline; the last
command removes it from the session):

```powershell
$env:MINSTROEM_API_KEY='<din-nøgle>'; .venv\Scripts\python.exe -m pytest tests/test_minstroem_live.py -m live -rA --tb=short -p no:cacheprovider; Remove-Item Env:MINSTROEM_API_KEY
```

It prints a `MIN STRØM LIVE SUMMARY` block (counts, time spans, a few rounded values, never the
key or the address) followed by one line per test; paste that back. Optionally set
`MINSTROEM_ADDRESS_ID` to pick the address (any case). The output holds no key, address id or
address text whatever `--tb` style you use, but `-l` / `--showlocals` prints every local variable,
the addresses included, so do not add it before pasting.

### Eloverblik

The [Eloverblik Customer API](https://eloverblik.dk/) serves your own **measured consumption
(and production)** per metering point, quarter-hourly up to yearly for the previous five years
plus the current one, together with the metering points, their master data and the charges
(subscriptions, tariffs, fees) linked to them. Create a token in the Eloverblik portal
(*Datadeling*, then *Opret token*), pass it as `refresh_token=` to `EloverblikClient` or set the
`ELOVERBLIK_REFRESH_TOKEN` environment variable; everything imports and constructs without one,
and the first request raises `MissingTokenError`. The client trades the refresh token for a
24-hour access token on its first request and reuses it; a 401 refreshes once and retries once.
`/token` allows **two calls a minute**, so pass one `client=` to several calls instead of letting
each create its own. Neither token appears in an exception message or a `repr`. Points are
requested at most 10 at a time; 429 and 503 are retried with backoff. Only the Customer API is
covered, not the Third-Party API, and nothing that writes.

```python
from energydata.eloverblik import EloverblikClient, get_meter_data, get_metering_points

with EloverblikClient() as client:
    points = get_metering_points(client=client)
    usage = get_meter_data("2026-10-01", "2026-11-01", aggregation="day", client=client)
print(usage.xs("energy_kwh", axis=1, level="field"))
```

`get_meter_data` returns a frame indexed by interval start (tz-aware `Europe/Copenhagen`, named
`time`) with two-level columns `(metering_point_id, field)`: `energy_kwh` (kWh, `float64`) and
`quality` (the raw code, such as `A04` measured; `A02` is "not available", with no quantity).
With `end` there is one row per interval of `[start, end)` on the Danish calendar (23 or 25
hours on a DST day); an interval the API did not return is **NaN**, never zero. Without `end` the
frame runs to the last interval returned. A bound not on the aggregation (for example
`aggregation="month"` starting mid-month) is a `ValueError`. **The interval holding today may be
incomplete**: it is a partial sum. Periods over 730 days are split on Danish calendar dates and
joined again. A point with no data, outside its data period, not covered by your authorization
or with an expired relation is NaN for the window concerned. An API error raises
`EloverblikError` carrying `status`, `error_code`, `error_text`, `metering_point_id` and
`correlation_id`.

| Function | Unit | Interval | Columns | Endpoint |
|---|---|---|---|---|
| `get_meter_data(start, end=None, *, aggregation="hour", metering_point_ids=None)` | kWh | `quarter`, `hour`, `day`, `month`, `year` | per point `energy_kwh`, `quality` | `POST /meterdata/gettimeseries/{from}/{to}/{aggregation}` |
| `get_metering_points(*, include_unlinked=False)` | | one row per point | `metering_point_id`, `type_of_mp`, `settlement_method`, `meter_reading_occurrence`, `balance_supplier_name`, address fields, `has_relation`, `is_moved_out`, `child_metering_point_ids` | `GET /meteringpoints/meteringpoints` |
| `get_metering_point_details(metering_point_ids=None)` | | one row per point | every scalar master-data field, snake_case (`type_of_mp`, `power_limit_kw_decimal`, ...) | `POST /meteringpoints/meteringpoint/getdetails` |
| `get_charges(metering_point_ids=None)` | DKK | one row per charge and price position | `metering_point_id`, `kind` (`subscription`, `tariff`, `fee`), `price_id`, `name`, `description`, `owner`, `period_type`, `valid_from`, `valid_to`, `price`, `quantity`, `position` | `POST /meteringpoints/meteringpoint/getcharges` |

`metering_point_ids` is one 18-digit id or a sequence of them; `None` means every linked point.
Consumer names and CPR/CVR numbers are deliberately left out of the point listing.

The live tests are marked `live`, are skipped without a token and are deselected by plain
`pytest`. Run them from the repo root in PowerShell (the token goes inline; the last command
removes it from the session):

```powershell
$env:ELOVERBLIK_REFRESH_TOKEN='<din-token>'; $env:ELOVERBLIK_LIVE_REPORT="$env:TEMP\eloverblik_report.txt"; .venv\Scripts\python.exe -m pytest tests/test_eloverblik_live.py -m live -rA --tb=short -p no:cacheprovider; Get-Content $env:ELOVERBLIK_LIVE_REPORT; Remove-Item Env:ELOVERBLIK_REFRESH_TOKEN
```

The report holds metering point ids only as `...` plus their last four digits, never a name,
an address, a CPR/CVR number or a token. `-l` / `--showlocals` prints every local variable, so do
not add it before pasting the output anywhere.

## Layout

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

This repo ships a Claude Code configuration under `.claude/`, plus `CLAUDE.md` (the routing
map, loaded every session) and `STRUCTURE.md` (every module and public signature, imported by
`CLAUDE.md`). The pipeline is the one from `petermads123/template`.

Ask for a feature in plain words, or `/feature <what to build>`; report a bug the same way,
or `/fix <symptom>`. Claude routes it and says which route in one line.

| Stage | Skill | What happens | Waits for you |
|---|---|---|---|
| Concept | `/feature` | The concept, the branch name and the version bump are agreed; repo scouts investigate in the background; `BRIEF.md` splits the work into rounds | the concept and the rounds |
| Diagnosis (a bug) | `/fix` | The symptom reproduced as a failing test, causes hunted from three angles, one root cause proven and criticised | the cause, the scope and the bump |
| Each round | `/round` | Conceptualize, plan, implement, verify with parallel reviewers, four test designers, write and run the tests the change can affect, three concept checkers, merge into the feature branch | only a question the round cannot answer |
| Finish | `/finish` | Whole-feature check, extras, the version, `STRUCTURE.md`, `README.md` and `DEVLOG.md` written, the pull request opened and seen through CI | only a critical extra |

State lives under `development/<branch>/` (`BRIEF.md`, `brief.json`, one `r<n>-<slug>/`
folder per round) and is committed after every step, so a session can be closed and resumed.
`development/README.md` documents the JSON files; the older numbered `NN-*.md` plan files
there are the history of the previous pipeline.

Claude never merges on its own judgment or on an approval alone: a pull request reaches
`main` because you pressed the button or told Claude to merge it. Every version is recorded
in `DEVLOG.md`.

### Hooks and CI

- **Session start** — reports a feature in flight and the skill that resumes it.
- **Before any shell command** — refuses a `git commit` or `git push` that would land on `main`.
- **After every `.py` write** — Ruff formats and auto-fixes the file.
- **CI** (`.github/workflows/check.yml`) — on every pull request and push to `main`, runs the
  four checks above and `python .claude/hooks/check_sync.py`, which checks that the version,
  `README.md`, `DEVLOG.md` and `STRUCTURE.md` agree with each other and the code. The live
  suites are deselected by `addopts`, so CI needs no API keys.

A round runs only the tests its change can affect (`python .claude/hooks/select_tests.py
--base <ref>` prints them); CI runs the whole suite.

Hooks are read at session start, so a change under `.claude/hooks/` or to
`.claude/settings.json` needs a Claude Code restart, and the first session asks for
workspace trust. The hooks run `python3` or `python` from your PATH and only use the
standard library; they locate `ruff`, `mypy` and `pytest` inside `.venv` themselves.

### Troubleshooting

`Activate.ps1 cannot be loaded because running scripts is disabled` — allow local scripts
for your user:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

`python` not found right after `winget install` — open a new terminal so PATH is reloaded.
