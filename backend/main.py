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
