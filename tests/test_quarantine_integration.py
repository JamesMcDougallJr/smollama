"""The model-facing tools, and how quarantine plugs into the loop and dashboard."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from smollama.detectors import Sample, Signal
from smollama.memory import LocalStore, MockEmbeddings, ObservationLoop
from smollama.quarantine import QuarantineConfig, QuarantineStore
from smollama.readings import Reading, ReadingManager
from smollama.tools.quarantine_tools import (
    ListStoppedSourcesTool,
    ResumeRecordingTool,
    StopRecordingTool,
)

NOW = datetime.now(timezone.utc)


def flat_samples(n=40, value=0.0, hours=10.0):
    step = timedelta(hours=hours) / (n - 1)
    return [Sample(ts=NOW - step * (n - 1 - i), value=value) for i in range(n)]


def varying_samples(n=40):
    step = timedelta(hours=10) / (n - 1)
    return [Sample(ts=NOW - step * (n - 1 - i), value=float(i % 6)) for i in range(n)]


@pytest.fixture
def qstore(tmp_path):
    s = QuarantineStore(str(tmp_path / "m.db"))
    s.connect()
    yield s
    s.close()


def loader_for(mapping):
    return lambda source_id: mapping.get(source_id, [])


class TestStopRecordingTool:
    @pytest.mark.asyncio
    async def test_it_quarantines_a_constant_source(self, qstore):
        tool = StopRecordingTool(qstore, loader_for({"hcsr04:distance": flat_samples()}))
        result = await tool.execute(source_id="hcsr04:distance",
                                    reason="reads exactly 0.0, sensor unplugged")
        assert result["status"] == "stopped"
        assert result["constant"] == 0.0
        assert qstore.quarantined_ids() == {"hcsr04:distance"}

    @pytest.mark.asyncio
    async def test_a_refusal_is_data_the_model_can_read_not_an_exception(self, qstore):
        """An exception would surface as a tool error and the model would likely
        retry blindly; a reason lets it correct course."""
        tool = StopRecordingTool(qstore, loader_for({"system:cpu_temp": varying_samples()}))
        result = await tool.execute(source_id="system:cpu_temp", reason="looks broken")
        assert result["status"] == "refused"
        assert "vary" in result["reason"] or "changing" in result["reason"]
        assert qstore.quarantined_ids() == set()

    @pytest.mark.asyncio
    async def test_an_unknown_source_is_refused(self, qstore):
        tool = StopRecordingTool(qstore, loader_for({}))
        result = await tool.execute(source_id="nope:nothing", reason="x")
        assert result["status"] == "refused"

    @pytest.mark.asyncio
    async def test_the_model_cannot_pass_in_its_own_evidence(self, qstore):
        """Extra arguments are ignored: evidence comes from stored history only."""
        tool = StopRecordingTool(qstore, loader_for({"system:cpu_temp": varying_samples()}))
        result = await tool.execute(
            source_id="system:cpu_temp", reason="x",
            samples=[0.0] * 100, constant=0.0, force=True, evidence_hours=99,
        )
        assert result["status"] == "refused"

    @pytest.mark.asyncio
    async def test_the_agent_is_recorded_as_the_actor(self, qstore):
        tool = StopRecordingTool(qstore, loader_for({"hcsr04:distance": flat_samples()}))
        await tool.execute(source_id="hcsr04:distance", reason="dead")
        assert qstore.events("hcsr04:distance")[0]["by"] == "agent"

    def test_the_schema_asks_for_only_a_source_and_a_reason(self, qstore):
        spec = StopRecordingTool(qstore, loader_for({})).to_ollama_format()["function"]
        assert spec["name"] == "stop_recording_source"
        assert set(spec["parameters"]["properties"]) == {"source_id", "reason"}
        assert set(spec["parameters"]["required"]) == {"source_id", "reason"}

    def test_the_description_states_when_not_to_use_it(self, qstore):
        """A model only knows the limits it is told, and the tool enforces them anyway."""
        d = StopRecordingTool(qstore, loader_for({})).description.lower()
        assert "constant" in d and "reversible" in d


class TestResumeAndListTools:
    @pytest.mark.asyncio
    async def test_resume_releases_a_source(self, qstore):
        await StopRecordingTool(qstore, loader_for({"hcsr04:distance": flat_samples()})).execute(
            source_id="hcsr04:distance", reason="dead")
        result = await ResumeRecordingTool(qstore).execute(
            source_id="hcsr04:distance", reason="sensor replaced")
        assert result["status"] == "resumed"
        assert qstore.quarantined_ids() == set()

    @pytest.mark.asyncio
    async def test_resuming_something_not_stopped_is_refused(self, qstore):
        result = await ResumeRecordingTool(qstore).execute(source_id="x:y", reason="r")
        assert result["status"] == "refused"

    @pytest.mark.asyncio
    async def test_list_reports_what_is_stopped_and_why(self, qstore):
        await StopRecordingTool(qstore, loader_for({"hcsr04:distance": flat_samples()})).execute(
            source_id="hcsr04:distance", reason="dead")
        out = await ListStoppedSourcesTool(qstore).execute()
        assert out["count"] == 1
        assert out["sources"][0]["source_id"] == "hcsr04:distance"
        assert out["sources"][0]["constant"] == 0.0


# ── the observation loop ────────────────────────────────────────────────────

def make_loop(tmp_path, readings, quarantine=None):
    store = LocalStore(str(tmp_path / "m.db"), "n", MockEmbeddings())
    store.connect()
    agent = MagicMock()
    agent.query = AsyncMock(return_value='{"observations": [], "memories": []}')
    loop = ObservationLoop(
        store=store, readings=readings, agent=agent, interval_minutes=15,
        lookback_minutes=60, use_detectors=True, quarantine=quarantine,
    )
    return loop, store, agent


def readings_manager(values):
    m = MagicMock(spec=ReadingManager)
    m.read_all = AsyncMock(return_value=[
        Reading("hcsr04", "distance", values["hcsr04"], datetime.now(), "cm"),
        Reading("system", "cpu_temp", values["cpu"], datetime.now(), "celsius"),
    ])
    return m


def with_no_signals():
    return patch.multiple(
        "smollama.memory.observation_loop",
        load_series=MagicMock(return_value={}),
        known_sources=MagicMock(return_value=[]),
        detect_all=MagicMock(return_value=[]),
    )


def logged_sources(store):
    conn = store._ensure_connected()
    return {r[0] for r in conn.execute("select distinct full_id from readings_log")}


class TestObservationLoop:
    @pytest.mark.asyncio
    async def test_a_quarantined_source_is_not_logged(self, tmp_path):
        q = QuarantineStore(str(tmp_path / "m.db")); q.connect()
        q.quarantine("hcsr04:distance", "dead", samples=flat_samples(), now=NOW)
        loop, store, _ = make_loop(tmp_path, readings_manager({"hcsr04": 0.0, "cpu": 48.0}), q)
        with with_no_signals():
            await loop.run_once()
        assert logged_sources(store) == {"system:cpu_temp"}

    @pytest.mark.asyncio
    async def test_a_changed_value_resumes_recording_in_the_same_cycle(self, tmp_path):
        q = QuarantineStore(str(tmp_path / "m.db")); q.connect()
        q.quarantine("hcsr04:distance", "dead", samples=flat_samples(), now=NOW)
        loop, store, _ = make_loop(tmp_path, readings_manager({"hcsr04": 42.5, "cpu": 48.0}), q)
        with with_no_signals():
            await loop.run_once()
        assert "hcsr04:distance" in logged_sources(store)
        assert q.quarantined_ids() == set()

    @pytest.mark.asyncio
    async def test_without_a_quarantine_store_everything_is_logged(self, tmp_path):
        loop, store, _ = make_loop(tmp_path, readings_manager({"hcsr04": 0.0, "cpu": 48.0}))
        with with_no_signals():
            await loop.run_once()
        assert logged_sources(store) == {"hcsr04:distance", "system:cpu_temp"}

    @pytest.mark.asyncio
    async def test_signals_on_a_quarantined_source_are_not_narrated(self, tmp_path):
        """The flatline that justified quarantine would otherwise fire every cycle,
        and the stale detector would fire too once recording stops — costing an
        inference per cycle about a source already known to be invalid."""
        q = QuarantineStore(str(tmp_path / "m.db")); q.connect()
        q.quarantine("hcsr04:distance", "dead", samples=flat_samples(), now=NOW)
        loop, store, agent = make_loop(tmp_path, readings_manager({"hcsr04": 0.0, "cpu": 48.0}), q)
        noise = [Signal("hcsr04:distance", "flatline", 5.0, "flat", "stuck", NOW),
                 Signal("hcsr04:distance", "stale", 5.0, "absent", "no data", NOW)]
        with patch.multiple(
            "smollama.memory.observation_loop",
            load_series=MagicMock(return_value={}),
            known_sources=MagicMock(return_value=[]),
            detect_all=MagicMock(return_value=noise),
        ):
            await loop.run_once()
        agent.query.assert_not_called()

    @pytest.mark.asyncio
    async def test_other_sources_are_still_narrated(self, tmp_path):
        q = QuarantineStore(str(tmp_path / "m.db")); q.connect()
        q.quarantine("hcsr04:distance", "dead", samples=flat_samples(), now=NOW)
        loop, store, agent = make_loop(tmp_path, readings_manager({"hcsr04": 0.0, "cpu": 48.0}), q)
        sigs = [Signal("hcsr04:distance", "flatline", 5.0, "flat", "stuck", NOW),
                Signal("system:cpu_temp", "level_shift", 4.0, "above", "cpu moved", NOW)]
        with patch.multiple(
            "smollama.memory.observation_loop",
            load_series=MagicMock(return_value={}),
            known_sources=MagicMock(return_value=[]),
            detect_all=MagicMock(return_value=sigs),
        ):
            await loop.run_once()
        prompt = agent.query.await_args[0][0]
        assert "cpu moved" in prompt and "stuck" not in prompt


# ── the dashboard ───────────────────────────────────────────────────────────

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from smollama.config import Config  # noqa: E402
from smollama.dashboard.app import create_app  # noqa: E402
from smollama.rules import RuleStore  # noqa: E402


@pytest.fixture
def dash(tmp_path):
    cfg = Config()
    cfg.memory.db_path = str(tmp_path / "memory.db")
    store = LocalStore(cfg.memory.db_path, "n", MockEmbeddings()); store.connect()
    rules = RuleStore(cfg.memory.db_path); rules.connect()
    q = QuarantineStore(cfg.memory.db_path); q.connect()
    q.quarantine("hcsr04:distance", "unplugged", samples=flat_samples(), now=NOW)
    client = TestClient(create_app(cfg, store=store, rules=rules, quarantine=q))
    yield client, q
    q.close(); rules.close(); store.close()


class TestDashboard:
    def test_the_rules_page_lists_quarantined_sources(self, dash):
        client, _ = dash
        text = client.get("/rules").text
        assert "hcsr04:distance" in text
        assert "Stopped recording" in text

    def test_a_human_can_release_a_source(self, dash):
        client, q = dash
        resp = client.post("/api/quarantine/release",
                           params={"source": "hcsr04:distance"}, follow_redirects=False)
        assert resp.status_code == 303
        assert q.quarantined_ids() == set()
        assert q.events("hcsr04:distance")[-1]["by"] == "human"

    def test_releasing_an_unknown_source_is_a_404(self, dash):
        client, _ = dash
        assert client.post("/api/quarantine/release",
                           params={"source": "no:such"}).status_code == 404

    def test_release_needs_a_quarantine_store(self, tmp_path):
        cfg = Config(); cfg.memory.db_path = str(tmp_path / "m.db")
        client = TestClient(create_app(cfg))
        assert client.post("/api/quarantine/release",
                           params={"source": "a:b"}).status_code == 503


# ── agent wiring ────────────────────────────────────────────────────────────

from smollama.agent import Agent  # noqa: E402


def agent_config(tmp_path, **memory):
    cfg = Config()
    cfg.memory.db_path = str(tmp_path / "memory.db")
    cfg.memory.embedding_provider = "mock"
    for k, v in memory.items():
        setattr(cfg.memory, k, v)
    return cfg


def tool_names(agent):
    return {t.name for t in agent._tools.list_tools()}


QUARANTINE_TOOLS = {"stop_recording_source", "resume_recording_source",
                    "list_stopped_sources"}


class TestAgentWiring:
    def test_the_agent_has_the_tools_by_default(self, tmp_path):
        assert QUARANTINE_TOOLS <= tool_names(Agent(agent_config(tmp_path)))

    def test_the_kill_switch_removes_them(self, tmp_path):
        """Off means the model is never offered the tool, not offered and refused."""
        agent = Agent(agent_config(tmp_path, quarantine_enabled=False))
        assert not (QUARANTINE_TOOLS & tool_names(agent))
        assert agent._quarantine is None

    def test_an_edge_node_has_no_memory_and_so_no_tools(self, tmp_path):
        cfg = agent_config(tmp_path)
        cfg.agent.mode = "edge"
        agent = Agent(cfg)
        assert not (QUARANTINE_TOOLS & tool_names(agent))

    def test_the_loop_is_given_the_store(self, tmp_path):
        agent = Agent(agent_config(tmp_path))
        assert agent._observation_loop._quarantine is agent._quarantine

    def test_config_knobs_reach_the_store(self, tmp_path):
        agent = Agent(agent_config(
            tmp_path, quarantine_max_sources=3, quarantine_min_flat_hours=12.0,
            quarantine_min_samples=50, quarantine_trickle_hours=2.0,
        ))
        c = agent._quarantine.config
        assert (c.max_sources, c.min_flat_hours, c.min_samples) == (3, 12.0, 50)
        assert c.trickle_seconds == 2 * 3600
