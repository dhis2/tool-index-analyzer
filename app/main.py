from pathlib import Path
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from typing import List

import app.queries as queries

app = FastAPI(title="DHIS2 Index Analyzer")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def _format_bytes(n: int | None) -> str:
    if not n:
        return "0 B"
    for unit, threshold in (("GB", 1_073_741_824), ("MB", 1_048_576), ("KB", 1_024)):
        if n >= threshold:
            return f"{n / threshold:.1f} {unit}"
    return f"{n} B"


def _format_number(n) -> str:
    if n is None:
        return "0"
    return f"{int(n):,}"


templates.env.filters["format_bytes"] = _format_bytes
templates.env.filters["format_number"] = _format_number


@app.get("/", response_class=HTMLResponse)
async def overview(request: Request):
    stats = queries.get_overview_stats()
    return templates.TemplateResponse(request, "overview.html", {"stats": stats})


@app.get("/dead", response_class=HTMLResponse)
async def dead_indexes(request: Request, analytics_only: bool = False):
    dead, insufficient = queries.get_dead_indexes(analytics_only=analytics_only)
    return templates.TemplateResponse(
        request,
        "dead.html",
        {"dead": dead, "insufficient": insufficient, "analytics_only": analytics_only},
    )


@app.get("/indexes", response_class=HTMLResponse)
async def all_indexes(
    request: Request,
    band: List[str] = Query(default=[]),
    analytics_only: bool = False,
):
    selected_bands = band if band else None
    indexes = queries.get_all_indexes(bands=selected_bands, analytics_only=analytics_only)
    return templates.TemplateResponse(
        request,
        "indexes.html",
        {
            "indexes": indexes,
            "selected_bands": band,
            "analytics_only": analytics_only,
            "all_bands": ["dead", "low", "medium", "high", "very_high"],
        },
    )


@app.get("/analytics", response_class=HTMLResponse)
async def analytics_families(request: Request):
    families = queries.get_analytics_families()
    return templates.TemplateResponse(request, "analytics.html", {"families": families})


@app.get("/analytics/{family}", response_class=HTMLResponse)
async def analytics_family_detail(request: Request, family: str):
    index_families = queries.get_analytics_index_family_summary(family)
    indexes = queries.get_analytics_family_detail(family)
    return templates.TemplateResponse(
        request,
        "analytics_family.html",
        {"family": family, "index_families": index_families, "indexes": indexes},
    )
