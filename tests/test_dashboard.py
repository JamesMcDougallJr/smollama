"""Tests for the web dashboard."""

import pytest
from datetime import datetime
from unittest.mock import MagicMock, AsyncMock

from smollama.config import Config, NodeConfig
from smollama.memory import LocalStore, MockEmbeddings
from smollama.readings import Reading, ReadingManager


# Only run tests if fastapi is installed
pytest.importorskip("fastapi")


from fastapi.testclient import TestClient
from smollama.dashboard import create_app


@pytest.fixture
def config():
    """Create a test configuration."""
    return Config(node=NodeConfig(name="test-dashboard-node"))


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
    manager.source_types = ["system"]
    manager.list_sources = MagicMock(return_value=["system:cpu_temp", "system:mem_percent"])
    return manager


@pytest.fixture
def app(config, mock_store, mock_readings):
    """Create the FastAPI app."""
    return create_app(config, store=mock_store, readings=mock_readings)


@pytest.fixture
def client(app):
    """Create a test client."""
    return TestClient(app)


class TestDashboardPages:
    """Tests for HTML page routes."""

    def test_index_page(self, client):
        """Test the main dashboard page."""
        response = client.get("/")

        assert response.status_code == 200
        assert "test-dashboard-node" in response.text
        assert "Dashboard" in response.text

    def test_readings_page(self, client):
        """Test the readings page."""
        response = client.get("/readings")

        assert response.status_code == 200
        assert "Readings" in response.text

    def test_observations_page(self, client):
        """Test the observations page."""
        response = client.get("/observations")

        assert response.status_code == 200
        assert "Observations" in response.text

    def test_memories_page(self, client):
        """Test the memories page."""
        response = client.get("/memories")

        assert response.status_code == 200
        assert "Memories" in response.text


class TestDashboardAPI:
    """Tests for JSON API routes."""

    def test_api_readings(self, client):
        """Test the readings API endpoint."""
        response = client.get("/api/readings")

        assert response.status_code == 200
        data = response.json()
        assert "readings" in data
        assert "timestamp" in data

    def test_api_observations(self, client):
        """Test the observations API endpoint."""
        response = client.get("/api/observations")

        assert response.status_code == 200
        data = response.json()
        assert "observations" in data
        assert "query" in data

    def test_api_observations_with_query(self, client):
        """Test observations API with search query."""
        response = client.get("/api/observations?query=temperature&limit=5")

        assert response.status_code == 200
        data = response.json()
        assert data["query"] == "temperature"

    def test_api_memories(self, client):
        """Test the memories API endpoint."""
        response = client.get("/api/memories")

        assert response.status_code == 200
        data = response.json()
        assert "memories" in data

    def test_api_stats(self, client):
        """Test the stats API endpoint."""
        response = client.get("/api/stats")

        assert response.status_code == 200
        data = response.json()
        assert "node_name" in data
        assert "timestamp" in data


class TestHTMXPartials:
    """Tests for HTMX partial routes."""

    def test_htmx_readings(self, client):
        """Test the HTMX readings partial."""
        response = client.get("/htmx/readings")

        assert response.status_code == 200
        # Should be HTML content
        assert "text/html" in response.headers.get("content-type", "")

    def test_htmx_observations(self, client):
        """Test the HTMX observations partial."""
        response = client.get("/htmx/observations")

        assert response.status_code == 200

    def test_htmx_stats(self, client):
        """Test the HTMX stats partial."""
        response = client.get("/htmx/stats")

        assert response.status_code == 200


class TestDashboardWithData:
    """Tests for dashboard with actual data."""

    def test_stats_with_data(self, client, mock_store):
        """Test stats include data from store."""
        # Add some data
        mock_store.add_observation("Test observation", "status", 0.9)
        mock_store.add_memory("Test memory", 0.85)

        response = client.get("/api/stats")
        data = response.json()

        assert data["observations_count"] >= 1
        assert data["memories_count"] >= 1

    def test_observations_search(self, client, mock_store):
        """Test observation search returns results."""
        mock_store.add_observation("Temperature is high", "anomaly", 0.9)
        mock_store.add_observation("Everything is normal", "status", 0.8)

        response = client.get("/api/observations?query=temperature")
        data = response.json()

        # Should find the temperature observation
        assert any("temperature" in obs["text"].lower() for obs in data["observations"])


class TestDashboardWithoutDependencies:
    """Tests for dashboard behavior without optional components."""

    def test_api_readings_no_manager(self, config):
        """Test readings API when no ReadingManager is provided."""
        app = create_app(config, store=None, readings=None)
        client = TestClient(app)

        response = client.get("/api/readings")
        data = response.json()

        assert "error" in data
        assert data["readings"] == []

    def test_api_observations_no_store(self, config):
        """Test observations API when no store is provided."""
        app = create_app(config, store=None, readings=None)
        client = TestClient(app)

        response = client.get("/api/observations")
        data = response.json()

        assert "error" in data
