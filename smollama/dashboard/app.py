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
from ..gpio_reader import GPIOReader, GPIO_AVAILABLE
from ..memory import LocalStore
from ..readings import ReadingManager

logger = logging.getLogger(__name__)

# Template directory
TEMPLATE_DIR = Path(__file__).parent / "templates"


def create_app(
    config: Config,
    store: LocalStore | None = None,
    readings: ReadingManager | None = None,
    gpio_reader: GPIOReader | None = None,
) -> FastAPI:
    """Create the FastAPI dashboard application.

    Args:
        config: Application configuration.
        store: Optional LocalStore for memory access.
        readings: Optional ReadingManager for live readings.
        gpio_reader: Optional GPIOReader for GPIO mode toggling.

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
    app.state.gpio_reader = gpio_reader

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

    @app.get("/api/health")
    async def api_health() -> dict[str, Any]:
        """Health check endpoint for monitoring and load balancers.

        Returns basic health status of dashboard components.
        Always returns 200 OK even if components are unavailable.
        """
        health = {
            "status": "ok",
            "timestamp": datetime.now().isoformat(),
            "node_name": config.node.name,
            "components": {
                "store": store is not None,
                "readings": readings is not None,
                "gpio": gpio_reader is not None,
            },
        }

        # Add readings health if available
        if readings:
            try:
                current = await readings.read_all()
                health["components"]["readings_count"] = len(current)
            except Exception as e:
                health["components"]["readings_error"] = str(e)

        # Add store health if available
        if store:
            try:
                stats = store.get_stats()
                health["components"]["store_observations"] = stats.get("observation_count", 0)
                health["components"]["store_memories"] = stats.get("memory_count", 0)
            except Exception as e:
                health["components"]["store_error"] = str(e)

        return health

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

        gpio_mock = gpio_reader.is_mock_mode if gpio_reader else True
        return templates.TemplateResponse(
            "partials/readings_list.html",
            {"request": request, "readings": current_readings, "gpio_mock": gpio_mock},
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

    @app.get("/htmx/gpio-toggle", response_class=HTMLResponse)
    async def htmx_gpio_toggle(request: Request):
        """HTMX partial for GPIO mode toggle."""
        has_gpio = gpio_reader is not None and len(gpio_reader.configured_pins) > 0
        mock_mode = gpio_reader.is_mock_mode if gpio_reader else True
        return templates.TemplateResponse(
            "partials/gpio_toggle.html",
            {
                "request": request,
                "has_gpio": has_gpio,
                "mock_mode": mock_mode,
                "gpio_available": GPIO_AVAILABLE,
                "error": None,
            },
        )

    @app.post("/api/gpio/mode", response_class=HTMLResponse)
    async def api_gpio_mode(request: Request):
        """Toggle GPIO mock/real mode."""
        form = await request.form()
        want_mock = form.get("mock", "true").lower() == "true"

        has_gpio = gpio_reader is not None and len(gpio_reader.configured_pins) > 0
        error = None
        mock_mode = True

        if gpio_reader:
            result = gpio_reader.set_mock_mode(want_mock)
            mock_mode = result["mock_mode"]
            error = result["error"]
        else:
            error = "No GPIO reader configured"

        return templates.TemplateResponse(
            "partials/gpio_toggle.html",
            {
                "request": request,
                "has_gpio": has_gpio,
                "mock_mode": mock_mode,
                "gpio_available": GPIO_AVAILABLE,
                "error": error,
            },
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
