"""Tests for the ObservationLoop background task."""

import asyncio
import json
import pytest
from datetime import datetime
from unittest.mock import MagicMock, AsyncMock, patch

from smollama.memory import LocalStore, MockEmbeddings, ObservationLoop
from smollama.readings import Reading, ReadingManager


@pytest.fixture
def mock_store():
    """Create an in-memory LocalStore."""
    store = LocalStore(":memory:", "test-node", MockEmbeddings())
    store.connect()
    yield store
    store.close()


@pytest.fixture
def mock_readings():
    """Create a mock ReadingManager."""
    manager = MagicMock(spec=ReadingManager)
    manager.read_all = AsyncMock(return_value=[
        Reading("system", "cpu_temp", 45.5, datetime.now(), "celsius"),
        Reading("system", "mem_percent", 67.2, datetime.now(), "percent"),
    ])
    return manager


@pytest.fixture
def mock_agent():
    """Create a mock Agent."""
    agent = MagicMock()
    agent.query = AsyncMock(return_value=json.dumps({
        "observations": [
            {
                "text": "CPU temperature is stable",
                "type": "status",
                "confidence": 0.9,
                "related_sources": ["system:cpu_temp"],
            }
        ],
        "memories": [
            {
                "fact": "Normal CPU temp range is 40-50C",
                "confidence": 0.85,
            }
        ],
    }))
    return agent


@pytest.fixture
def observation_loop(mock_store, mock_readings, mock_agent):
    """Create an ObservationLoop for testing."""
    return ObservationLoop(
        store=mock_store,
        readings=mock_readings,
        agent=mock_agent,
        interval_minutes=15,
        lookback_minutes=60,
    )


class TestObservationLoopInit:
    """Tests for ObservationLoop initialization."""

    def test_init_parameters(self, mock_store, mock_readings, mock_agent):
        """Test ObservationLoop stores parameters correctly."""
        loop = ObservationLoop(
            store=mock_store,
            readings=mock_readings,
            agent=mock_agent,
            interval_minutes=30,
            lookback_minutes=120,
        )

        assert loop._store == mock_store
        assert loop._readings == mock_readings
        assert loop._agent == mock_agent
        assert loop._interval == 30 * 60  # Converted to seconds
        assert loop._lookback == 120
        assert loop._running is False
        assert loop._task is None


class TestObservationLoopStartStop:
    """Tests for start/stop lifecycle."""

    @pytest.mark.asyncio
    async def test_start_sets_running(self, observation_loop):
        """Test start() sets running flag."""
        # Patch sleep to avoid waiting
        with patch("asyncio.sleep", return_value=None):
            await observation_loop.start()
            assert observation_loop._running is True
            assert observation_loop._task is not None
            await observation_loop.stop()

    @pytest.mark.asyncio
    async def test_stop_clears_task(self, observation_loop):
        """Test stop() clears task and flag."""
        with patch("asyncio.sleep", return_value=None):
            await observation_loop.start()
            await observation_loop.stop()

            assert observation_loop._running is False
            assert observation_loop._task is None

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self, observation_loop):
        """Test calling start() twice doesn't create duplicate tasks."""
        with patch("asyncio.sleep", return_value=None):
            await observation_loop.start()
            task1 = observation_loop._task

            await observation_loop.start()
            task2 = observation_loop._task

            assert task1 is task2
            await observation_loop.stop()

    @pytest.mark.asyncio
    async def test_stop_without_start(self, observation_loop):
        """Test stop() is safe to call without start()."""
        await observation_loop.stop()  # Should not raise
        assert observation_loop._running is False


class TestObservationLoopGeneration:
    """Tests for observation generation."""

    @pytest.mark.asyncio
    async def test_run_once_gathers_readings(
        self, observation_loop, mock_readings, mock_store
    ):
        """Test run_once reads all sources."""
        await observation_loop.run_once()

        mock_readings.read_all.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_once_logs_readings(
        self, observation_loop, mock_store
    ):
        """Test run_once logs readings to database."""
        initial_count = mock_store.get_stats()["readings_count"]

        await observation_loop.run_once()

        final_count = mock_store.get_stats()["readings_count"]
        assert final_count > initial_count

    @pytest.mark.asyncio
    async def test_run_once_calls_agent(
        self, observation_loop, mock_agent
    ):
        """Test run_once queries the agent with prompt."""
        await observation_loop.run_once()

        mock_agent.query.assert_called_once()
        # Check prompt contains expected content
        prompt = mock_agent.query.call_args[0][0]
        assert "readings" in prompt.lower()
        assert "observations" in prompt.lower()

    @pytest.mark.asyncio
    async def test_run_once_stores_observations(
        self, observation_loop, mock_store
    ):
        """Test run_once stores observations from LLM response."""
        await observation_loop.run_once()

        stats = mock_store.get_stats()
        assert stats["observations_count"] >= 1

    @pytest.mark.asyncio
    async def test_run_once_stores_memories(
        self, observation_loop, mock_store
    ):
        """Test run_once stores memories from LLM response."""
        await observation_loop.run_once()

        stats = mock_store.get_stats()
        assert stats["memories_count"] >= 1

    @pytest.mark.asyncio
    async def test_run_once_no_readings(
        self, mock_store, mock_agent
    ):
        """Test run_once handles no readings gracefully."""
        empty_readings = MagicMock(spec=ReadingManager)
        empty_readings.read_all = AsyncMock(return_value=[])

        loop = ObservationLoop(
            store=mock_store,
            readings=empty_readings,
            agent=mock_agent,
            interval_minutes=15,
            lookback_minutes=60,
        )

        await loop.run_once()

        # Should skip LLM query with no readings
        mock_agent.query.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_once_no_llm_response(
        self, observation_loop, mock_agent, mock_store
    ):
        """Test run_once handles empty LLM response."""
        mock_agent.query = AsyncMock(return_value=None)

        initial_obs = mock_store.get_stats()["observations_count"]

        await observation_loop.run_once()

        # No new observations should be added
        final_obs = mock_store.get_stats()["observations_count"]
        assert final_obs == initial_obs


class TestResponseProcessing:
    """Tests for LLM response processing."""

    @pytest.mark.asyncio
    async def test_process_json_response(
        self, observation_loop, mock_agent, mock_store
    ):
        """Test processing a valid JSON response."""
        mock_agent.query = AsyncMock(return_value=json.dumps({
            "observations": [
                {"text": "Test observation", "type": "status", "confidence": 0.8}
            ],
            "memories": [],
        }))

        await observation_loop.run_once()

        assert mock_store.get_stats()["observations_count"] == 1

    @pytest.mark.asyncio
    async def test_process_json_with_markdown(
        self, observation_loop, mock_agent, mock_store
    ):
        """Test processing JSON wrapped in markdown code blocks."""
        mock_agent.query = AsyncMock(return_value="""```json
{
    "observations": [{"text": "Test", "type": "status", "confidence": 0.8}],
    "memories": []
}
```""")

        await observation_loop.run_once()

        assert mock_store.get_stats()["observations_count"] == 1

    @pytest.mark.asyncio
    async def test_process_invalid_json_stores_nothing(
        self, observation_loop, mock_agent, mock_store
    ):
        """An unparseable response is discarded, not stored as observation text.

        Storing it used to put JSON fragments and essay preambles into the store
        as observation text; since observations are embedded and fed back in as
        "relevant past observations", that junk compounded into later prompts.
        """
        mock_agent.query = AsyncMock(return_value="This is not valid JSON at all")

        await observation_loop.run_once()

        assert mock_store.get_stats()["observations_count"] == 0

    @pytest.mark.asyncio
    async def test_process_truncated_json_stores_nothing(
        self, observation_loop, mock_agent, mock_store
    ):
        """The real failure mode: generation cut off mid-object."""
        mock_agent.query = AsyncMock(
            return_value='```json\n{\n  "observations": [\n    {\n      "text": "The hcsr04'
        )

        await observation_loop.run_once()

        assert mock_store.get_stats()["observations_count"] == 0

    @pytest.mark.asyncio
    async def test_observation_query_is_capped_and_structured(
        self, observation_loop, mock_agent, mock_store
    ):
        """The LLM call must bound generation and demand JSON, without tools."""
        mock_agent.query = AsyncMock(
            return_value=json.dumps({"observations": [], "memories": []})
        )

        await observation_loop.run_once()

        assert mock_agent.query.await_count >= 1
        kwargs = mock_agent.query.await_args.kwargs
        # A JSON schema, not the looser format="json"
        assert isinstance(kwargs["format"], dict)
        assert kwargs["format"]["properties"]["observations"]["maxItems"] == 3
        assert kwargs["options"]["num_predict"] > 0
        assert kwargs["use_tools"] is False
        # Must override the node's conversational system prompt
        from smollama.memory.observation_loop import OBSERVATION_SYSTEM_PROMPT
        assert kwargs["system"] == OBSERVATION_SYSTEM_PROMPT
        assert "not an assistant" in kwargs["system"]


class TestObservationPassIsConfigurable:
    """The pass shape must be tunable for a model more capable than a 1-2B local one."""

    @staticmethod
    def _loop(mock_store, mock_readings, mock_agent, **kw):
        return ObservationLoop(
            store=mock_store, readings=mock_readings, agent=mock_agent,
            interval_minutes=15, lookback_minutes=60, **kw,
        )

    @pytest.mark.asyncio
    async def test_tools_can_be_enabled(self, mock_store, mock_readings, mock_agent):
        loop = self._loop(mock_store, mock_readings, mock_agent, use_tools=True)
        await loop.run_once()
        assert mock_agent.query.await_args.kwargs["use_tools"] is True

    @pytest.mark.asyncio
    async def test_tools_win_over_schema_when_both_requested(
        self, mock_store, mock_readings, mock_agent, caplog
    ):
        """A schema pins decoding, leaving no room for tool calls — tools win, loudly."""
        loop = self._loop(mock_store, mock_readings, mock_agent,
                          use_tools=True, structured_output=True)
        assert "mutually exclusive" in caplog.text
        await loop.run_once()
        kwargs = mock_agent.query.await_args.kwargs
        assert kwargs["use_tools"] is True
        assert kwargs["format"] is None

    @pytest.mark.asyncio
    async def test_structured_output_can_be_disabled(
        self, mock_store, mock_readings, mock_agent
    ):
        loop = self._loop(mock_store, mock_readings, mock_agent, structured_output=False)
        await loop.run_once()
        assert mock_agent.query.await_args.kwargs["format"] is None

    @pytest.mark.asyncio
    async def test_system_prompt_node_inherits_agent_persona(
        self, mock_store, mock_readings, mock_agent
    ):
        """"node" means: don't override, let the agent use its configured prompt."""
        loop = self._loop(mock_store, mock_readings, mock_agent, system_prompt="node")
        await loop.run_once()
        assert mock_agent.query.await_args.kwargs["system"] is None

    @pytest.mark.asyncio
    async def test_system_prompt_literal_override(
        self, mock_store, mock_readings, mock_agent
    ):
        loop = self._loop(mock_store, mock_readings, mock_agent,
                          system_prompt="You are a lab instrument.")
        await loop.run_once()
        assert mock_agent.query.await_args.kwargs["system"] == "You are a lab instrument."

    @pytest.mark.asyncio
    async def test_num_predict_zero_means_uncapped(
        self, mock_store, mock_readings, mock_agent
    ):
        loop = self._loop(mock_store, mock_readings, mock_agent, num_predict=0)
        await loop.run_once()
        assert mock_agent.query.await_args.kwargs["options"] is None

    @pytest.mark.asyncio
    async def test_process_empty_observations(
        self, observation_loop, mock_agent, mock_store
    ):
        """Test processing response with empty observations."""
        mock_agent.query = AsyncMock(return_value=json.dumps({
            "observations": [],
            "memories": [],
        }))

        await observation_loop.run_once()

        # No observations from response (but may have system readings logged)
        assert mock_store.get_stats()["observations_count"] == 0


class TestPromptFormatting:
    """Tests for prompt generation."""

    @pytest.mark.asyncio
    async def test_format_current_readings(self, observation_loop):
        """Test current readings formatting."""
        readings = [
            Reading("system", "cpu_temp", 45.5, datetime.now(), "celsius"),
            Reading("gpio", "17", 1, datetime.now(), "boolean"),
        ]

        formatted = observation_loop._format_current_readings(readings)

        assert "system:cpu_temp" in formatted
        assert "45.5" in formatted
        assert "celsius" in formatted
        assert "gpio:17" in formatted

    def test_format_current_readings_empty(self, observation_loop):
        """Test formatting with no readings."""
        formatted = observation_loop._format_current_readings([])
        assert "no readings" in formatted.lower()

    def test_format_history_empty(self, observation_loop):
        """Test history formatting with no data."""
        formatted = observation_loop._format_history([])
        assert "no" in formatted.lower()

    def test_format_past_observations_empty(self, observation_loop):
        """Test past observations formatting with no data."""
        formatted = observation_loop._format_past_observations([])
        assert "no" in formatted.lower()

    def test_format_past_observations_with_data(self, observation_loop):
        """Test past observations formatting."""
        observations = [
            {"text": "CPU is hot", "type": "anomaly"},
            {"text": "System stable", "type": "status"},
        ]

        formatted = observation_loop._format_past_observations(observations)

        assert "[anomaly]" in formatted
        assert "CPU is hot" in formatted
        assert "[status]" in formatted
