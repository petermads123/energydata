"""The addresses of a Min Strøm account."""

import pandas as pd

from ._series import _addresses, _client_scope
from .client import MinStroemClient, _showcase_has_key


def get_addresses(*, client: MinStroemClient | None = None) -> pd.DataFrame:
    """Get the account's addresses.

    | Property | Value |
    |---|---|
    | Endpoint | `GET /me/addresses` |
    | Index | `RangeIndex`, one row per address |
    | Text columns | `id`, `label`, `formatted_address`, `region`, `time_zone`, `grid_operator_name`, `supplier_name` (object; `None` where the API has null) |
    | Capability columns | `consumption`, `solar_export`, `consumption_costs`, `solar_export_revenue`, `prices`, `forecast_prices` (`bool`) |

    Args:
        client: A client to fetch with. `None` creates one and closes it before
            returning; a passed client is left open.

    Returns:
        One row per address; an empty frame with the same columns when the
        account has none.

    Raises:
        MissingApiKeyError: If there is no API key.
        MinStroemError: If the API refuses the request or returns an address
            with a missing field or a non-boolean capability.
        RuntimeError: If a passed `client` is closed.
    """
    with _client_scope(client) as active:
        return active.run(lambda: _addresses(active))


def main() -> None:
    """Showcase this module's functionality (calls the live API)."""
    if not _showcase_has_key():
        return

    addresses = get_addresses()

    print(f"{len(addresses)} address(es)")
    print(addresses[["region", "forecast_prices", "prices", "consumption"]])


if __name__ == "__main__":
    main()
