from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import CORS_ORIGINS
from routers import (analyze, investors, thesis, portfolio, screener,
                     ownership, search, reports)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(analyze.router)
app.include_router(investors.router)
app.include_router(thesis.router)
app.include_router(portfolio.router)
app.include_router(screener.router)
app.include_router(ownership.router)
app.include_router(search.router)
app.include_router(reports.router)


# ---------------------------------------------------------------------------
# Warmup — pre-populate caches for the most common first requests so the first
# real user after a cold start (Render free tier sleeps after ~15 min idle)
# doesn't pay the full uncached cost across every service at once.
# ---------------------------------------------------------------------------

WARMUP_TICKERS = ["AAPL", "MSFT", "NVDA"]


def _warmup(tickers=None):
    results = {}
    for t in (tickers or WARMUP_TICKERS):
        for name, fn in (
            ("analyze", lambda t=t: analyze.analyze(t)),
            ("investors", lambda t=t: investors.investors_endpoint(t)),
            ("thesis", lambda t=t: thesis.thesis_endpoint(t)),
        ):
            try:
                fn()
                results[f"{name}:{t}"] = "ok"
            except Exception as e:  # never let warmup crash anything
                results[f"{name}:{t}"] = f"err: {str(e)[:60]}"
    return results


@app.get("/warmup")
def warmup():
    """Manually warm the caches (also called automatically ~30s after startup)."""
    return {"warmed": _warmup([WARMUP_TICKERS[0]])}  # AAPL only for a fast manual hit


@app.get("/health")
def health():
    return {"status": "ok"}


@app.on_event("startup")
def _schedule_warmup():
    """Fire a background warmup 30s after uvicorn starts (non-blocking), so the
    cache is warm before the first real user hits it. Runs once per process."""
    import threading

    def _run():
        _warmup()  # all WARMUP_TICKERS: analyze/investors/thesis

    timer = threading.Timer(30.0, _run)
    timer.daemon = True
    timer.start()
