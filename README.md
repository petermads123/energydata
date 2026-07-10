# energydata

Python package for collecting Danish energy data — including, but not limited to,
energy prices and energy consumption — from:

- **[Energi Data Service](https://www.energidataservice.dk/)** — open API from
  Energinet with spot prices, tariffs, production/consumption statistics, etc.
- **[Eloverblik](https://eloverblik.dk/)** — personal metering-point data
  (consumption per metering point; requires a data-access token).

Install via:

    pip install git+https://github.com/petermads123/energydata.git@main

or as a dependency in `pyproject.toml`:

    "energydata @ git+https://github.com/petermads123/energydata.git@main"

## Development

1. Have git installed on your machine.
2. Have python installed on your machine. At least the version specified in pyproject.toml
3. Have the python extension installed in your VSCode
4. CTRL + Shift + P -> Python: Create Environment -> Venv -> Select python version
5. Download dependencies by running "pip install -e ." in the powershell terminal. Environment should activate automatically when powershell terminal is launched.

### Ruff
For proper development, please have Ruff installed.
After installement; go to File -> Preferences -> Settings.
    Turn on "Format on save".

### Checks

```powershell
.venv/Scripts/python.exe -m mypy energydata    # type check
.venv/Scripts/python.exe -m ruff check .       # lint
.venv/Scripts/python.exe -m pytest             # tests
```
