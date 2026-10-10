"""FastAPI web dashboard application."""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
)
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
    observers: list | None = None,
    frames: Any = None,
    rules: Any = None,
) -> FastAPI:
    """Create the FastAPI dashboard application.

    Args:
        config: Application configuration.
        store: Optional LocalStore for memory access.
        readings: Optional ReadingManager for live readings.
        gpio_reader: Optional GPIOReader for GPIO mode toggling.
        observers: Optional loaded ObservationDomain plugins for the
                   live domain-status card (e.g. vision).
        frames: Optional FrameStore for camera keyframe search.

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
    app.state.observers = observers or []
    app.state.frames = frames

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
            observations = store.search_observations("", limit=50, from_ts=from_ts)
            # Attach any human verdict so the keep/dismiss control reflects state
            # rather than always rendering as unrated.
            for obs in observations:
                obs["verdict"] = store.get_feedback(obs["id"])
            context["observations"] = observations

        return templates.TemplateResponse(request, "observations.html", context)

    @app.get("/rules", response_class=HTMLResponse)
    async def rules_page(request: Request):
        """Rule browser: live uncovered signals plus rules grouped by state.

        Proposed rules need a human decision, so this page is the approval surface
        that keeps an automatically-authored rule from becoming load-bearing.
        """
        context = {
            "node_name": config.node.name,
            "page": "rules",
            "rules_available": rules is not None,
            "signals": [],
            "grouped": {},
            "any_rules": False,
        }

        if rules is not None:
            from ..detectors import DetectorConfig, dedupe_correlated, detect_all
            from ..detectors.source import known_sources, load_series
            from ..rules import uncovered_signals

            all_rules = rules.all_rules()
            context["any_rules"] = bool(all_rules)
            # Ordered so the states needing attention come first.
            context["grouped"] = {
                state: [r for r in all_rules if r.state == state]
                for state in ("proposed", "active", "muted", "parked", "retired")
            }

            try:
                # Same window, staleness registry and dedup as the observation
                # loop, so this page shows what the loop would actually narrate
                # rather than a differently-configured second opinion.
                db = config.memory.db_path
                series = load_series(
                    db, window_seconds=config.memory.detector_window_hours * 3600
                )
                signals = detect_all(
                    series, config=DetectorConfig(),
                    expected_sources=known_sources(db),
                )
                context["signals"] = dedupe_correlated(
                    uncovered_signals(signals, rules)
                )
            except Exception as e:
                logger.warning("could not compute signals for rules page: %s", e)

        return templates.TemplateResponse(request, "rules.html", context)

    @app.post("/api/rules/{rule_id}/{action}")
    async def api_rule_action(rule_id: int, action: str):
        """Promote, mute, or retire a rule from the dashboard.

        The verb is in the path rather than a form field so no request-body parsing
        is needed — `request.form()` requires python-multipart, which is not a
        dependency of the dashboard extra.

        Mute and retire require a reason in the store, so these supply one naming the
        operator: the reason log is the only audit trail for why a rule stopped
        monitoring.
        """
        if rules is None:
            raise HTTPException(status_code=503, detail="rule store not connected")
        if rules.get(rule_id) is None:
            raise HTTPException(status_code=404, detail=f"no rule {rule_id}")

        if action == "promote":
            rules.promote(rule_id, "promoted from dashboard")
        elif action == "mute":
            rules.mute(rule_id, "muted from dashboard")
        elif action == "retire":
            rules.retire(rule_id, "retired from dashboard")
        else:
            raise HTTPException(status_code=400, detail=f"unknown action {action!r}")

        return RedirectResponse("/rules", status_code=303)

    @app.post("/api/signals/{action}")
    async def api_signal_action(
        action: str, source: str, detector: str, direction: str
    ):
        """Create a rule from a live signal — the only way one can come into being.

        `author_rules` is the only other caller of `store.propose`, and nothing
        calls it. Without this endpoint no rule could exist, so `uncovered_signals`
        filtered nothing and every recurring signal cost an inference every cycle
        forever. On the live master that was six signals, indefinitely.

        `watch` promotes immediately because only *active* rules cover a signal;
        proposing alone would leave it still being narrated, which is not what
        clicking a button on a noisy signal means. `propose` is there for the
        cautious path — it lands in the proposed list for a later decision.

        Identity comes from query params rather than the path because a `full_id`
        contains colons and reads badly segmented, and rather than a form body
        because `request.form()` needs python-multipart.
        """
        if rules is None:
            raise HTTPException(status_code=503, detail="rule store not connected")
        if action not in ("watch", "propose"):
            raise HTTPException(status_code=400, detail=f"unknown action {action!r}")

        rule = rules.propose(
            source, detector, direction,
            rationale=f"created from a live signal on the dashboard ({action})",
        )
        if action == "watch":
            # Only promote a fresh proposal. Re-proposing is documented not to
            # resurrect state, and routing around that here would let one click
            # silently un-mute a rule someone had judged wrong.
            if rule.state == "proposed":
                rules.promote(rule.id, "watched from dashboard")
            else:
                logger.info(
                    "signal rule %s is %s, leaving it alone", rule.id, rule.state
                )

        return RedirectResponse("/rules", status_code=303)

    @app.post("/api/observations/{observation_id}/feedback/{verdict}")
    async def api_observation_feedback(observation_id: int, verdict: str, request: Request):
        """Record a keep/dismiss verdict on an observation.

        This is the only ground truth the system has about whether an observation was
        worth surfacing. Without it, any automated judgement about rule quality is
        optimising a proxy.

        Verdict is in the path for the same reason as the rule actions above: no
        body parsing, so no python-multipart dependency.
        """
        if store is None:
            raise HTTPException(status_code=503, detail="store not connected")
        try:
            store.record_feedback(observation_id, verdict)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return RedirectResponse(
            request.headers.get("referer", "/observations"), status_code=303
        )

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

    @app.get("/frames", response_class=HTMLResponse)
    async def frames_page(request: Request, query: str = ""):
        """Camera keyframe search page."""
        context = {
            "node_name": config.node.name,
            "page": "frames",
            "query": query,
            "frames_enabled": frames is not None,
        }

        if frames:
            result = frames.search_text(query, limit=24)
            context["search_mode"] = result["mode"]
            context["frames"] = result["results"]
            context["frame_stats"] = frames.get_stats()

        return templates.TemplateResponse(request, "frames.html", context)

    @app.get("/htmx/frames", response_class=HTMLResponse)
    async def htmx_frames(request: Request, query: str = ""):
        """HTMX partial for frame search results."""
        context = {"frames": [], "search_mode": None, "query": query}
        if frames:
            result = frames.search_text(query, limit=24)
            context["search_mode"] = result["mode"]
            context["frames"] = result["results"]
        return templates.TemplateResponse(
            request, "partials/frames_results.html", context
        )

    @app.get("/activity", response_class=HTMLResponse)
    async def activity_page(
        request: Request, category: str = "", hours: int = 24, min_score: float | None = None
    ):
        """Zero-shot activity triage review page."""
        context = {
            "node_name": config.node.name,
            "page": "activity",
            "category": category,
            "hours": hours,
            "min_score": min_score,
            "frames_enabled": frames is not None,
        }
        if frames:
            context["windows"] = frames.recent_activity(
                hours=hours, category=category or None, min_score=min_score
            )
            context["categories"] = frames.activity_categories()
        return templates.TemplateResponse(request, "activity.html", context)

    @app.get("/htmx/activity", response_class=HTMLResponse)
    async def htmx_activity(
        request: Request, category: str = "", hours: int = 24, min_score: float | None = None
    ):
        """HTMX partial for the activity review list."""
        context = {"windows": [], "category": category, "hours": hours}
        if frames:
            context["windows"] = frames.recent_activity(
                hours=hours, category=category or None, min_score=min_score
            )
        return templates.TemplateResponse(
            request, "partials/activity_results.html", context
        )

    @app.get("/frames/thumb/{frame_id}")
    async def frame_thumbnail(frame_id: int):
        """Serve a stored keyframe thumbnail."""
        if not frames:
            raise HTTPException(status_code=404, detail="Frame search not enabled")
        frame = frames.get_frame(frame_id)
        if not frame:
            raise HTTPException(status_code=404, detail="Frame not found")
        path = frames.thumbnail_path(frame)
        if not path:
            raise HTTPException(status_code=404, detail="No thumbnail for this frame")
        return FileResponse(path, media_type="image/jpeg")

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
                health["components"]["store_observations"] = stats.get("observations_count", 0)
                health["components"]["store_memories"] = stats.get("memories_count", 0)
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

    @app.get("/observations/{observation_id}", response_class=HTMLResponse)
    async def observation_detail_page(request: Request, observation_id: int):
        """Observation detail / provenance page."""
        if not store:
            raise HTTPException(status_code=503, detail="No memory store available")
        obs = store.get_observation_by_id(observation_id)
        if obs is None:
            raise HTTPException(status_code=404, detail="Observation not found")
        return templates.TemplateResponse(
            request,
            "observation_detail.html",
            {"node_name": config.node.name, "page": "observations", "obs": obs},
        )

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

    @app.get("/htmx/domain-status", response_class=HTMLResponse)
    async def htmx_domain_status(request: Request):
        """HTMX partial for observation-domain live status (vision, audio, ...).

        Status is derived on demand from stored reading history via each
        domain's pure derive_state/derive_status, so it works even though the
        dashboard runs in a separate process from the agent.
        """
        statuses = []
        domains = app.state.observers
        if store and domains:
            for domain in domains:
                try:
                    history = [
                        h for h in store.get_recent_readings(
                            minutes=domain.status_lookback_minutes,
                        )
                        if domain.matches(h["full_id"])
                    ]
                    state = domain.derive_state(history)
                    statuses.append({
                        "domain": domain.domain_name,
                        "status": domain.derive_status(state),
                        "has_data": state.get("has_data", False),
                    })
                except Exception as e:
                    logger.error(f"Domain status for '{domain.domain_name}' failed: {e}")

        return templates.TemplateResponse(
            request,
            "partials/domain_status.html",
            {"statuses": statuses},
        )

    return app
