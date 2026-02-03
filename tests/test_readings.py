"""Tests for the unified readings abstraction layer."""

import pytest
from datetime import datetime
from unittest.mock import MagicMock, AsyncMock, patch, mock_open

from smollama.readings import (
    Reading,
    ReadingProvider,
    ReadingManager,
    SystemReadingProvider,
    GPIOReadingProvider,
)
from smollama.config import GPIOConfig, GPIOPinConfig
from smollama.gpio_reader import GPIOReader


class TestReadingDataclass:
    """Tests for the Reading dataclass."""

    def test_reading_creation(self):
        """Test basic Reading creation."""
        reading = Reading(
            source_type="system",
            source_id="cpu_temp",
            value=45.5,
            timestamp=datetime.now(),
            unit="celsius",
        )

        assert reading.source_type == "system"
        assert reading.source_id == "cpu_temp"
        assert reading.value == 45.5
        assert reading.unit == "celsius"

    def test_reading_full_id(self):
        """Test full_id property."""
        reading = Reading(
            source_type="gpio",
            source_id="17",
            value=1,
            timestamp=datetime.now(),
        )

        assert reading.full_id == "gpio:17"

    def test_reading_full_id_mqtt(self):
        """Test full_id with complex source_id."""
        reading = Reading(
            source_type="mqtt",
            source_id="kitchen/temperature",
            value=22.5,
            timestamp=datetime.now(),
        )

        assert reading.full_id == "mqtt:kitchen/temperature"

    def test_reading_to_log_dict(self):
        """Test serialization to dict."""
        now = datetime.now()
        reading = Reading(
            source_type="system",
            source_id="cpu_temp",
            value=45.5,
            timestamp=now,
            unit="celsius",
            metadata={"zone": "thermal_zone0"},
        )

        log_dict = reading.to_log_dict()

        assert log_dict["source_type"] == "system"
        assert log_dict["source_id"] == "cpu_temp"
        assert log_dict["full_id"] == "system:cpu_temp"
        assert log_dict["value"] == 45.5
        assert log_dict["timestamp"] == now.isoformat()
        assert log_dict["unit"] == "celsius"
        assert log_dict["metadata"] == {"zone": "thermal_zone0"}

    def test_reading_optional_fields(self):
        """Test Reading with optional fields as None."""
        reading = Reading(
            source_type="test",
            source_id="123",
            value="test_value",
            timestamp=datetime.now(),
        )

        assert reading.unit is None
        assert reading.metadata is None


class TestReadingManager:
    """Tests for the ReadingManager class."""

    def test_register_provider(self):
        """Test registering a reading provider."""
        manager = ReadingManager()

        # Create a mock provider
        mock_provider = MagicMock(spec=ReadingProvider)
        mock_provider.source_type = "test"
        mock_provider.available_sources = ["1", "2"]

        manager.register(mock_provider)

        assert "test" in manager.source_types

    def test_unregister_provider(self):
        """Test unregistering a provider."""
        manager = ReadingManager()

        mock_provider = MagicMock(spec=ReadingProvider)
        mock_provider.source_type = "test"

        manager.register(mock_provider)
        manager.unregister("test")

        assert "test" not in manager.source_types

    def test_unregister_nonexistent(self):
        """Test unregistering a non-existent provider."""
        manager = ReadingManager()
        # Should not raise
        manager.unregister("nonexistent")

    @pytest.mark.asyncio
    async def test_read_valid_source(self):
        """Test reading from a valid source."""
        manager = ReadingManager()

        mock_reading = Reading("test", "1", 42, datetime.now())
        mock_provider = MagicMock(spec=ReadingProvider)
        mock_provider.source_type = "test"
        mock_provider.read = AsyncMock(return_value=mock_reading)

        manager.register(mock_provider)

        result = await manager.read("test:1")

        assert result == mock_reading
        mock_provider.read.assert_called_once_with("1")

    @pytest.mark.asyncio
    async def test_read_invalid_format(self):
        """Test reading with invalid full_id format."""
        manager = ReadingManager()

        result = await manager.read("invalid_no_colon")

        assert result is None

    @pytest.mark.asyncio
    async def test_read_unknown_provider(self):
        """Test reading from unknown provider."""
        manager = ReadingManager()

        result = await manager.read("unknown:123")

        assert result is None

    @pytest.mark.asyncio
    async def test_read_all(self):
        """Test reading from all providers."""
        manager = ReadingManager()

        # Create two mock providers with async read_all
        mock1 = MagicMock(spec=ReadingProvider)
        mock1.source_type = "type1"
        mock1.read_all = AsyncMock(
            return_value=[Reading("type1", "1", 10, datetime.now())]
        )

        mock2 = MagicMock(spec=ReadingProvider)
        mock2.source_type = "type2"
        mock2.read_all = AsyncMock(
            return_value=[Reading("type2", "2", 20, datetime.now())]
        )

        manager.register(mock1)
        manager.register(mock2)

        results = await manager.read_all()

        assert len(results) == 2

    def test_list_sources_all(self):
        """Test listing all sources."""
        manager = ReadingManager()

        mock1 = MagicMock(spec=ReadingProvider)
        mock1.source_type = "gpio"
        mock1.available_sources = ["17", "27"]

        mock2 = MagicMock(spec=ReadingProvider)
        mock2.source_type = "system"
        mock2.available_sources = ["cpu_temp", "mem_percent"]

        manager.register(mock1)
        manager.register(mock2)

        sources = manager.list_sources()

        assert len(sources) == 4
        assert "gpio:17" in sources
        assert "gpio:27" in sources
        assert "system:cpu_temp" in sources
        assert "system:mem_percent" in sources

    def test_list_sources_filtered(self):
        """Test listing sources filtered by type."""
        manager = ReadingManager()

        mock1 = MagicMock(spec=ReadingProvider)
        mock1.source_type = "gpio"
        mock1.available_sources = ["17", "27"]

        mock2 = MagicMock(spec=ReadingProvider)
        mock2.source_type = "system"
        mock2.available_sources = ["cpu_temp"]

        manager.register(mock1)
        manager.register(mock2)

        sources = manager.list_sources(source_type="gpio")

        assert len(sources) == 2
        assert all(s.startswith("gpio:") for s in sources)

    def test_source_types_property(self):
        """Test source_types property."""
        manager = ReadingManager()

        mock1 = MagicMock(spec=ReadingProvider)
        mock1.source_type = "gpio"

        mock2 = MagicMock(spec=ReadingProvider)
        mock2.source_type = "system"

        manager.register(mock1)
        manager.register(mock2)

        types = manager.source_types

        assert "gpio" in types
        assert "system" in types


class TestSystemReadingProvider:
    """Tests for SystemReadingProvider with mocked /sys files."""

    def test_source_type(self):
        """Test source_type property."""
        provider = SystemReadingProvider()
        assert provider.source_type == "system"

    def test_available_sources(self):
        """Test available_sources property."""
        provider = SystemReadingProvider()
        sources = provider.available_sources

        assert "cpu_temp" in sources
        assert "cpu_freq" in sources
        assert "mem_percent" in sources
        assert "mem_available_mb" in sources
        assert "load_avg" in sources

    @pytest.mark.asyncio
    async def test_read_cpu_temp_success(self):
        """Test reading CPU temperature with mocked file."""
        provider = SystemReadingProvider()

        with patch("builtins.open", mock_open(read_data="45500")):
            reading = await provider.read("cpu_temp")

        assert reading is not None
        assert reading.source_id == "cpu_temp"
        assert reading.value == 45.5
        assert reading.unit == "celsius"

    @pytest.mark.asyncio
    async def test_read_cpu_temp_file_not_found(self):
        """Test CPU temp returns 0 when file not found."""
        provider = SystemReadingProvider()

        with patch("builtins.open", side_effect=FileNotFoundError()):
            reading = await provider.read("cpu_temp")

        assert reading is not None
        assert reading.value == 0.0

    @pytest.mark.asyncio
    async def test_read_unknown_source(self):
        """Test reading unknown source returns None."""
        provider = SystemReadingProvider()

        reading = await provider.read("unknown_metric")

        assert reading is None

    @pytest.mark.asyncio
    async def test_read_load_avg(self):
        """Test reading load average."""
        provider = SystemReadingProvider()

        with patch("builtins.open", mock_open(read_data="0.45 0.32 0.21 1/234 12345")):
            reading = await provider.read("load_avg")

        assert reading is not None
        assert reading.value == 0.45
        assert reading.unit == "load"

    @pytest.mark.asyncio
    async def test_read_all(self):
        """Test reading all system metrics."""
        provider = SystemReadingProvider()

        # Mock all file reads - need to be careful with multiple opens
        # Use side_effect to return different values for different files
        readings = await provider.read_all()

        # Should return 5 readings regardless of file access success
        assert len(readings) == 5
        assert all(r.source_type == "system" for r in readings)


class TestGPIOReadingProvider:
    """Tests for GPIOReadingProvider."""

    @pytest.fixture
    def gpio_reader(self):
        """Create a mock-mode GPIO reader."""
        config = GPIOConfig(
            pins=[
                GPIOPinConfig(pin=17, name="motion", mode="input"),
                GPIOPinConfig(pin=27, name="door", mode="input"),
            ],
            mock=True,
        )
        return GPIOReader(config)

    @pytest.fixture
    def gpio_provider(self, gpio_reader):
        """Create a GPIO provider."""
        return GPIOReadingProvider(gpio_reader)

    def test_source_type(self, gpio_provider):
        """Test source_type property."""
        assert gpio_provider.source_type == "gpio"

    def test_available_sources(self, gpio_provider):
        """Test available_sources returns pin numbers."""
        sources = gpio_provider.available_sources

        assert "17" in sources
        assert "27" in sources

    @pytest.mark.asyncio
    async def test_read_valid_pin(self, gpio_provider):
        """Test reading a valid GPIO pin."""
        reading = await gpio_provider.read("17")

        assert reading is not None
        assert reading.source_type == "gpio"
        assert reading.source_id == "17"
        assert reading.value in (0, 1)
        assert reading.unit == "boolean"
        assert reading.metadata["name"] == "motion"

    @pytest.mark.asyncio
    async def test_read_invalid_pin(self, gpio_provider):
        """Test reading invalid pin returns None."""
        reading = await gpio_provider.read("99")
        assert reading is None

    @pytest.mark.asyncio
    async def test_read_invalid_pin_format(self, gpio_provider):
        """Test reading non-numeric pin ID."""
        reading = await gpio_provider.read("not_a_number")
        assert reading is None

    @pytest.mark.asyncio
    async def test_read_all(self, gpio_provider):
        """Test reading all GPIO pins."""
        readings = await gpio_provider.read_all()

        assert len(readings) == 2
        assert all(r.source_type == "gpio" for r in readings)

        pin_ids = [r.source_id for r in readings]
        assert "17" in pin_ids
        assert "27" in pin_ids
