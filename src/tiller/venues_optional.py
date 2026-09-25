"""Optional venues (Hyperliquid perps, CEX spot): configuration gate only.

No SDK is installed and no order code exists. Enabling a venue requires the geo
acknowledgement in the config (L0), prints the terms-of-service warning, and every code
path that would use the venue raises ``NotImplementedError('optional venue not built')``.
"""

from __future__ import annotations

from typing import Literal

from tiller.config import GEO_ACK_TEXT, Config, OptVenue

VenueName = Literal["hyperliquid", "cex"]

GEO_WARNING = (
    "WARNING: optional venue enabled. Hyperliquid's terms of service (15 June 2026) bar US and "
    "Ontario persons; Binance/Bybit/OKX/Bitget copy products are unavailable to US persons. "
    f"You must be able to state truthfully: '{GEO_ACK_TEXT}'. Tiller ships NO order code for "
    "these venues: this module only documents the extension point and refuses to run."
)
NOT_BUILT = "optional venue not built"


def enabled_venues(cfg: Config) -> list[VenueName]:
    out: list[VenueName] = []
    if cfg.venues.hyperliquid.enabled:
        out.append("hyperliquid")
    if cfg.venues.cex.enabled:
        out.append("cex")
    return out


def check_optional_venues(cfg: Config) -> None:
    """Print the geo warning and raise ``NotImplementedError`` if any optional venue is enabled."""
    names = enabled_venues(cfg)
    if not names:
        return
    print(GEO_WARNING)
    raise NotImplementedError(f"{NOT_BUILT}: {', '.join(names)}")


class OptionalVenue:
    """Placeholder for a venue that is deliberately not implemented."""

    def __init__(self, name: VenueName, section: OptVenue) -> None:
        if section.geo_ack != GEO_ACK_TEXT:
            raise ValueError(f"venues.{name}: geo_ack must equal {GEO_ACK_TEXT!r}")
        print(GEO_WARNING)
        raise NotImplementedError(f"{NOT_BUILT}: {name}")
