"""Single source of truth for ticker symbol normalization.

Different data providers spell share-class / special tickers differently:

    Display   yfinance   FMP      Finnhub   SEC/EDGAR
    BRK.B     BRK-B      BRK-B    BRK.B     BRK-B
    BF.B      BF-B       BF-B     BF.B      BF-B
    HEI.A     HEI-A      HEI-A    HEI.A     HEI-A

Yahoo, FMP and the SEC ticker map use a DASH for the class separator; Finnhub
accepts either. Historically each provider was queried with the raw user input,
so any dot-class ticker (BRK.B, BF.B, HEI.A, RDS.A, ...) silently failed on
yfinance/FMP/SEC and only Finnhub's quote-only path answered — so Berkshire
showed no valuation at all.

`normalize_ticker(raw)` returns a small record with the display symbol plus the
exact string each provider expects, so every call site converts consistently
instead of patching providers one by one.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TickerForms:
    display: str   # what the user typed / what we show (dot form, upper)
    yf: str        # yfinance / SEC / EDGAR / CIK map  (dash form)
    fmp: str       # Financial Modeling Prep           (dash form)
    finnhub: str   # Finnhub                           (dot form; accepts either)
    sec: str       # SEC ticker->CIK map               (dash form)


def normalize_ticker(raw: str) -> TickerForms:
    """Normalize a user-supplied ticker into provider-specific spellings.
    Idempotent and safe on already-normalized input; uppercases and trims."""
    display = (raw or "").strip().upper()
    dash = display.replace(".", "-")
    return TickerForms(
        display=display,
        yf=dash,
        fmp=dash,
        finnhub=display,  # Finnhub accepts the dot form (and the dash); keep display
        sec=dash,
    )
