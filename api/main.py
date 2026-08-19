"""
main.py — BI Validator FastAPI application.

Run with:
    uvicorn api.main:app --reload --port 8000

From the project root (test_playwright combined/).
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import configs, discovery, export, runs, test_cases

app = FastAPI(
    title="Automated BI Testing - Validation API",
    description="Backend for the Automated BI Testing - Validation control panel",
    version="1.0.0",
)

# Allow the frontend (localhost:3000 / Vite dev) to call the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(runs.router)
app.include_router(export.router)
app.include_router(test_cases.router)
app.include_router(configs.router)
app.include_router(discovery.router)


@app.get("/health")
def health():
    return {"status": "ok", "service": "BI Validator API"}
