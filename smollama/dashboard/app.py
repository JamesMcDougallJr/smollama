"""FastAPI web dashboard application."""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import Config
from ..gpio_reader import GPIOReader, GPIO_AVAILABLE
from ..memory import LocalStore
from ..readings import ReadingManager, Reading

logger = logging.getLogger(__name__)

# Template directory
TEMPLATE_DIR = Path(__file__).parent / "templates"


def _local_source_types(readings_manager: ReadingManager) -> set[str]:
    """Source types that belong to the local node (all registered providers except mqtt_edge)."""
    return {st for st in readings_manager.source_types if st != "mqtt_edge"}


def _compute_node_status(node_readings: list[Reading]) -> str:
    """Return 'active', 'stale', or 'offline' based on the age of the most recent reading."""
    if not node_readings:
        return "offline"
    latest = max(r.timestamp for r in node_readings)
    now = datetime.now() if latest.tzinfo is None else datetime.now(timezone.utc)
    age = (now - latest).total_seconds()
    if age < 30:
        return "active"
    elif age < 300:
        return "stale"
    return "offline"


def _build_node_info(all_readings: list[Reading], readings_manager: ReadingManager, config: Any) -> dict:
    """Build node categorization data for the filter bar and detail pages."""
    local_types = _local_source_types(readings_manager)
    edge_names = sorted({r.source_type for r in all_readings if r.source_type not in local_types})
    edge_nodes = []
    for name in edge_names:
        nr = [r for r in all_readings if r.source_type == name]
        edge_nodes.append({
            "name": name,
            "status": _compute_node_status(nr),
            "last_seen": max(r.timestamp for r in nr).isoformat() if nr else None,
            "count": len(nr),
        })
    return {"local": config.node.name, "edge": edge_nodes}


def _to_reading_dict(r: Reading, local_types: set[str]) -> dict:
    """Serialise a Reading to the dict shape used by templates."""
    is_local = r.source_type in local_types
    return {
        "full_id": r.full_id,
        "value": r.value,
        "unit": r.unit,
        "timestamp": r.timestamp.isoformat(),
        "source_type": r.source_type,
        "node_label": "Local" if is_local else r.source_type,
        "is_local": is_local,
    }


def create_app(
    config: Config,
    store: LocalStore | None = None,
    readings: ReadingManager | None = None,
    gpio_reader: GPIOReader | None = None,
    discovery_manager: Any = None,
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
    app.state.discovery_manager = discovery_manager

    # Set up Jinja2 templates
    templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

    # ==================== HTML Routes ====================

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        """Main dashboard page."""
        context = {
            "node_name": config.node.name,
            "page": "index",
        }

        # Get current stats if store available
        if store:
            context["stats"] = store.get_stats()

        return templates.TemplateResponse(request, "index.html", context)

    @app.get("/readings", response_class=HTMLResponse)
    async def readings_page(request: Request):
        """Live readings page."""
        context = {
            "node_name": config.node.name,
            "page": "readings",
        }

        if readings:
            try:
                current = await readings.read_all()
                local_types = _local_source_types(readings)
                context["readings"] = [_to_reading_dict(r, local_types) for r in current]
                context["nodes"] = _build_node_info(current, readings, config)
            except Exception as e:
                logger.error(f"Failed to get readings: {e}")
                context["readings"] = []
                context["error"] = str(e)

        return templates.TemplateResponse(request, "readings.html", context)

    @app.get("/observations", response_class=HTMLResponse)
    async def observations_page(request: Request, hours: int = 0, query: str = ""):
        """Observation history page."""
        from_ts = None
        if hours > 0:
            from_ts = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        context = {
            "node_name": config.node.name,
            "page": "observations",
            "query": query,
            "hours": hours,
        }

        if store:
            context["observations"] = store.search_observations("", limit=50, from_ts=from_ts)

        return templates.TemplateResponse(request, "observations.html", context)

    @app.get("/memories", response_class=HTMLResponse)
    async def memories_page(request: Request):
        """Memory browser page."""
        context = {
            "node_name": config.node.name,
            "page": "memories",
        }

        if store:
            context["memories"] = store.search_memories("", limit=50)

        return templates.TemplateResponse(request, "memories.html", context)

    # ==================== API Routes (JSON) ====================

    @app.get("/api/readings")
    async def api_readings() -> dict[str, Any]:
        """Get current readings as JSON."""
        if not readings:
            return JSONResponse({"error": "No reading manager available", "readings": []}, status_code=503)

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
            return JSONResponse({"error": str(e), "readings": []}, status_code=503)

    @app.get("/api/observations")
    async def api_observations(
        query: str = "",
        limit: int = 20,
        obs_type: str | None = None,
        hours: int = 0,
    ) -> dict[str, Any]:
        """Search observations."""
        if not store:
            return JSONResponse({"error": "No memory store available", "observations": []}, status_code=503)

        from_ts = None
        if hours > 0:
            from_ts = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        observations = store.search_observations(
            query=query,
            limit=limit,
            observation_type=obs_type,
            from_ts=from_ts,
        )

        return {
            "query": query,
            "hours": hours,
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
            return JSONResponse({"error": "No memory store available", "memories": []}, status_code=503)

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
    async def htmx_readings(request: Request, node: str = ""):
        """HTMX partial for live readings update, with optional node filter."""
        current_readings = []
        if readings:
            try:
                current = await readings.read_all()
                local_types = _local_source_types(readings)
                if node == "local":
                    current = [r for r in current if r.source_type in local_types]
                elif node:
                    current = [r for r in current if r.source_type == node]
                current_readings = [_to_reading_dict(r, local_types) for r in current]
            except Exception:
                pass

        gpio_mock = gpio_reader.is_mock_mode if gpio_reader else True
        return templates.TemplateResponse(
            request,
            "partials/readings_list.html",
            {"readings": current_readings, "gpio_mock": gpio_mock},
        )

    @app.get("/nodes/{node_name}", response_class=HTMLResponse)
    async def node_detail_page(request: Request, node_name: str):
        """Node detail / drill-down page."""
        context = {
            "node_name": config.node.name,
            "page": "readings",
            "detail_node": node_name,
        }

        if readings:
            try:
                current = await readings.read_all()
                local_types = _local_source_types(readings)
                is_local = node_name == "local" or node_name == config.node.name

                if node_name == "local":
                    node_readings = [r for r in current if r.source_type in local_types]
                else:
                    node_readings = [r for r in current if r.source_type == node_name]

                context["is_local"] = is_local
                context["status"] = _compute_node_status(node_readings)
                context["last_seen"] = (
                    max(r.timestamp for r in node_readings).isoformat() if node_readings else None
                )
                context["reading_count"] = len(node_readings)
                context["readings"] = [_to_reading_dict(r, local_types) for r in node_readings]
            except Exception as e:
                logger.error(f"Failed to get node readings for {node_name}: {e}")
                context["readings"] = []
                context["error"] = str(e)

        return templates.TemplateResponse(request, "node_detail.html", context)

    @app.get("/htmx/observations", response_class=HTMLResponse)
    async def htmx_observations(request: Request, query: str = "", hours: int = 0):
        """HTMX partial for observations list."""
        from_ts = None
        if hours > 0:
            from_ts = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        observations = []
        if store:
            observations = store.search_observations(query, limit=50, from_ts=from_ts)

        return templates.TemplateResponse(
            request,
            "partials/observations_list.html",
            {"observations": observations},
        )

    @app.get("/htmx/memories", response_class=HTMLResponse)
    async def htmx_memories(request: Request, query: str = ""):
        """HTMX partial for memories list."""
        memories = store.search_memories(query, limit=20) if store else []
        return templates.TemplateResponse(
            request,
            "partials/memories_list.html",
            {"memories": memories},
        )

    @app.get("/htmx/gpio-toggle", response_class=HTMLResponse)
    async def htmx_gpio_toggle(request: Request):
        """HTMX partial for GPIO mode toggle."""
        has_gpio = gpio_reader is not None and len(gpio_reader.configured_pins) > 0
        mock_mode = gpio_reader.is_mock_mode if gpio_reader else True
        return templates.TemplateResponse(
            request,
            "partials/gpio_toggle.html",
            {
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
            request,
            "partials/gpio_toggle.html",
            {
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
            request,
            "partials/stats.html",
            {"stats": stats},
        )

    return app
