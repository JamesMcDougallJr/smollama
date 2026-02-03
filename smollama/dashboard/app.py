"""FastAPI web dashboard application."""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import Config
from ..memory import LocalStore
from ..readings import ReadingManager

logger = logging.getLogger(__name__)

# Template directory
TEMPLATE_DIR = Path(__file__).parent / "templates"


def create_app(
    config: Config,
    store: LocalStore | None = None,
    readings: ReadingManager | None = None,
) -> FastAPI:
    """Create the FastAPI dashboard application.

    Args:
        config: Application configuration.
        store: Optional LocalStore for memory access.
        readings: Optional ReadingManager for live readings.

    Returns:
        Configured FastAPI application.
    """
    app = FastAPI(
        title="Smollama Dashboard",
        description="Local monitoring dashboard for Smollama nodes",
        version="0.1.0",
    )

    # Store references for route handlers
    app.state.config = config
    app.state.store = store
    app.state.readings = readings

    # Set up Jinja2 templates
    templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

    # ==================== HTML Routes ====================

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        """Main dashboard page."""
        context = {
            "request": request,
            "node_name": config.node.name,
            "page": "index",
        }

        # Get current stats if store available
        if store:
            context["stats"] = store.get_stats()

        return templates.TemplateResponse("index.html", context)

    @app.get("/readings", response_class=HTMLResponse)
    async def readings_page(request: Request):
        """Live readings page."""
        context = {
            "request": request,
            "node_name": config.node.name,
            "page": "readings",
        }

        # Get current readings if available
        if readings:
            try:
                current = await readings.read_all()
                context["readings"] = [
                    {
                        "full_id": r.full_id,
                        "value": r.value,
                        "unit": r.unit,
                        "timestamp": r.timestamp.isoformat(),
                    }
                    for r in current
                ]
            except Exception as e:
                logger.error(f"Failed to get readings: {e}")
                context["readings"] = []
                context["error"] = str(e)

        return templates.TemplateResponse("readings.html", context)

    @app.get("/observations", response_class=HTMLResponse)
    async def observations_page(request: Request):
        """Observation history page."""
        context = {
            "request": request,
            "node_name": config.node.name,
            "page": "observations",
        }

        if store:
            # Get recent observations (search with empty query returns all)
            context["observations"] = store.search_observations("", limit=50)

        return templates.TemplateResponse("observations.html", context)

    @app.get("/memories", response_class=HTMLResponse)
    async def memories_page(request: Request):
        """Memory browser page."""
        context = {
            "request": request,
            "node_name": config.node.name,
            "page": "memories",
        }

        if store:
            context["memories"] = store.search_memories("", limit=50)

        return templates.TemplateResponse("memories.html", context)

    # ==================== API Routes (JSON) ====================

    @app.get("/api/readings")
    async def api_readings() -> dict[str, Any]:
        """Get current readings as JSON."""
        if not readings:
            return {"error": "No reading manager available", "readings": []}

        try:
            current = await readings.read_all()
            return {
                "timestamp": datetime.now().isoformat(),
                "readings": [
                    {
                        "full_id": r.full_id,
                        "value": r.value,
                        "unit": r.unit,
                        "timestamp": r.timestamp.isoformat(),
                        "metadata": r.metadata,
                    }
                    for r in current
                ],
            }
        except Exception as e:
            return {"error": str(e), "readings": []}

    @app.get("/api/observations")
    async def api_observations(
        query: str = "",
        limit: int = 20,
        obs_type: str | None = None,
    ) -> dict[str, Any]:
        """Search observations."""
        if not store:
            return {"error": "No memory store available", "observations": []}

        observations = store.search_observations(
            query=query,
            limit=limit,
            observation_type=obs_type,
        )

        return {
            "query": query,
            "count": len(observations),
            "observations": observations,
        }

    @app.get("/api/memories")
    async def api_memories(
        query: str = "",
        limit: int = 20,
    ) -> dict[str, Any]:
        """Search memories."""
        if not store:
            return {"error": "No memory store available", "memories": []}

        memories = store.search_memories(query=query, limit=limit)

        return {
            "query": query,
            "count": len(memories),
            "memories": memories,
        }

    @app.get("/api/stats")
    async def api_stats() -> dict[str, Any]:
        """Get system statistics."""
        stats = {
            "node_name": config.node.name,
            "timestamp": datetime.now().isoformat(),
        }

        if store:
            stats.update(store.get_stats())

        if readings:
            stats["source_types"] = readings.source_types
            stats["source_count"] = len(readings.list_sources())

        return stats

    # ==================== HTMX Partials ====================

    @app.get("/htmx/readings", response_class=HTMLResponse)
    async def htmx_readings(request: Request):
        """HTMX partial for live readings update."""
        current_readings = []
        if readings:
            try:
                current = await readings.read_all()
                current_readings = [
                    {
                        "full_id": r.full_id,
                        "value": r.value,
                        "unit": r.unit,
                    }
                    for r in current
                ]
            except Exception:
                pass

        return templates.TemplateResponse(
            "partials/readings_list.html",
            {"request": request, "readings": current_readings},
        )

    @app.get("/htmx/observations", response_class=HTMLResponse)
    async def htmx_observations(request: Request, query: str = ""):
        """HTMX partial for observations list."""
        observations = []
        if store:
            observations = store.search_observations(query, limit=20)

        return templates.TemplateResponse(
            "partials/observations_list.html",
            {"request": request, "observations": observations},
        )

    @app.get("/htmx/stats", response_class=HTMLResponse)
    async def htmx_stats(request: Request):
        """HTMX partial for stats update."""
        stats = {}
        if store:
            stats = store.get_stats()

        return templates.TemplateResponse(
            "partials/stats.html",
            {"request": request, "stats": stats},
        )

    return app
