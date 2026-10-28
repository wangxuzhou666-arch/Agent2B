"""Fixed benchmark settings and the owner-maintained price table.

The data, the clock and the principal are pinned so every run (any model, any machine) sees the
same database and asks as the same non-admin user.
"""

import json
import math
from datetime import UTC, date, datetime, time
from pathlib import Path

from twotrack.core import Principal
from twotrack.providers.openai_compat import DEFAULT_ROUTE_TIMEOUT_SECONDS

RNG_SEED = 42
SCALE = 1.0
TODAY = date(2026, 10, 1)
PRINCIPAL = Principal(user_id="bench", roles=frozenset({"operator"}))

DEFAULT_MAX_TURNS = 6  # baseline model calls per question; the last one is offered no tools
# Seconds a model call may take when TWOTRACK_LLM_TIMEOUT_SECONDS is unset: every call but
# twotrack's routing opinion (the baseline's turns, twotrack's narration, chat and agent steps).
DEFAULT_TIMEOUT = 60.0
# twotrack's routing opinion when TWOTRACK_LLM_ROUTE_TIMEOUT_SECONDS is unset: the product's own
DEFAULT_ROUTE_TIMEOUT = DEFAULT_ROUTE_TIMEOUT_SECONDS

# USD per million tokens, keyed by the model id passed to --model. Left empty on purpose: the
# owner fills it in (or passes --prices FILE with the same shape) before approving a budget.
# The harness never guesses a price; a model without an entry reports cost as "n/a".
#   "<model id>": {"input_per_mtok": <float>, "output_per_mtok": <float>}
PRICES: dict[str, dict[str, float | None]] = {}


def clock_for(today: date) -> datetime:
    """The fixed 'now' every tool sees: 09:00 UTC on the seed's anchor date."""
    return datetime.combine(today, time(9, 0), tzinfo=UTC)


def load_prices(path: str | Path | None) -> dict[str, dict[str, float | None]]:
    """The built-in table, overridden by a JSON file of the same shape when one is given."""
    table = {k: dict(v) for k, v in PRICES.items()}
    if path:
        extra = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(extra, dict):
            raise ValueError("a price file must be a JSON object keyed by model id")
        for model, entry in extra.items():
            table[str(model)] = {"input_per_mtok": entry.get("input_per_mtok"),
                                 "output_per_mtok": entry.get("output_per_mtok")}
    return table


def estimated_cost(prices: dict[str, dict[str, float | None]], model: str | None,
                   prompt_tokens: int, completion_tokens: int) -> float | None:
    """USD for the given token counts, or None when the model has no complete price entry."""
    entry = prices.get(model or "")
    if not entry:
        return None
    price_in, price_out = entry.get("input_per_mtok"), entry.get("output_per_mtok")
    if price_in is None or price_out is None:
        return None
    cost = (prompt_tokens * float(price_in) + completion_tokens * float(price_out)) / 1e6
    return cost if math.isfinite(cost) else None
