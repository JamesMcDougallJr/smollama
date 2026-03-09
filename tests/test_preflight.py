"""Tests for preflight checks and config auto-discovery."""

import os
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

from smollama.config import (
    Config,
    NodeConfig,
    OllamaConfig,
    MQTTConfig,
    MQTTTopicsConfig,
    MemoryConfig,
    SyncConfig,
    Mem0Config,
    DiscoveryConfig,
    _discover_config_path,
    load_config,
)
from smollama.preflight import (
    PreflightResult,
    run_preflight,
    _check_ollama,
    _check_mqtt,
    _check_mem0,
    _check_sync,
)


@pytest.fixture
def base_config():
    """Create a minimal test config."""
    return Config(
        node=NodeConfig(name="test-node"),
        ollama=OllamaConfig(host="localhost", port=11434, model="llama3.2:1b"),
        mqtt=MQTTConfig(
            broker="localhost",
            port=1883,
            topics=MQTTTopicsConfig(subscribe=["smollama/broadcast"]),
        ),
        memory=MemoryConfig(embedding_provider="ollama", embedding_model="all-minilm:l6-v2"),
        sync=SyncConfig(enabled=False),
        mem0=Mem0Config(enabled=False),
        discovery=DiscoveryConfig(enabled=False),
    )


# --- Config auto-discovery tests ---


class TestConfigDiscovery:
    def test_discover_config_yaml_in_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.yaml").write_text("node:\n  name: found\n")
        result = _discover_config_path()
        assert result is not None
        assert result.name == "config.yaml"

    def test_discover_config_yml_in_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.yml").write_text("node:\n  name: found\n")
        result = _discover_config_path()
        assert result is not None
        assert result.name == "config.yml"

    def test_discover_yaml_preferred_over_yml(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.yaml").write_text("node:\n  name: yaml\n")
        (tmp_path / "config.yml").write_text("node:\n  name: yml\n")
        result = _discover_config_path()
        assert result.name == "config.yaml"

    def test_discover_home_dir(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        smollama_dir = tmp_path / ".smollama"
        smollama_dir.mkdir()
        (smollama_dir / "config.yaml").write_text("node:\n  name: home\n")
        with patch.object(Path, "home", return_value=tmp_path):
            result = _discover_config_path()
        assert result is not None
        assert "config.yaml" in str(result)

    def test_discover_none_when_no_config(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch.object(Path, "home", return_value=tmp_path):
            result = _discover_config_path()
        assert result is None

    def test_load_config_auto_discovers(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.yaml").write_text("node:\n  name: discovered\n")
        config = load_config(None)
        assert config.node.name == "discovered"

    def test_load_config_uses_defaults_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch.object(Path, "home", return_value=tmp_path):
            config = load_config(None)
        assert config.node.name == "smollama-node"


# --- Preflight check tests ---


class TestPreflightOllama:
    @pytest.mark.asyncio
    async def test_ollama_healthy_model_available(self, base_config):
        result = PreflightResult()
        with patch("smollama.preflight.OllamaClient") as MockClient:
            instance = MockClient.return_value
            instance.check_connection = AsyncMock(return_value=True)
            instance.list_models = AsyncMock(return_value=["llama3.2:1b", "all-minilm:l6-v2"])
            await _check_ollama(base_config, result)

        assert result.passed is True
        assert result.errors == []
        assert result.actions_taken == []

    @pytest.mark.asyncio
    async def test_ollama_unreachable(self, base_config):
        result = PreflightResult()
        with patch("smollama.preflight.OllamaClient") as MockClient:
            instance = MockClient.return_value
            instance.check_connection = AsyncMock(return_value=False)
            await _check_ollama(base_config, result)

        assert result.passed is False
        assert len(result.errors) == 1
        assert "not reachable" in result.errors[0]

    @pytest.mark.asyncio
    async def test_model_missing_auto_pull_cli(self, base_config):
        result = PreflightResult()
        with (
            patch("smollama.preflight.OllamaClient") as MockClient,
            patch("smollama.preflight.shutil.which", return_value="/usr/bin/ollama"),
            patch("smollama.preflight.subprocess.run") as mock_run,
        ):
            instance = MockClient.return_value
            instance.check_connection = AsyncMock(return_value=True)
            instance.list_models = AsyncMock(return_value=[])
            mock_run.return_value = MagicMock(returncode=0)
            await _check_ollama(base_config, result)

        assert result.passed is True
        assert any("Pulled Ollama model" in a for a in result.actions_taken)

    @pytest.mark.asyncio
    async def test_model_missing_auto_pull_library_fallback(self, base_config):
        result = PreflightResult()
        with (
            patch("smollama.preflight.OllamaClient") as MockClient,
            patch("smollama.preflight.shutil.which", return_value=None),
        ):
            instance = MockClient.return_value
            instance.check_connection = AsyncMock(return_value=True)
            instance.list_models = AsyncMock(return_value=[])
            instance.pull_model = AsyncMock(return_value=True)
            await _check_ollama(base_config, result)

        assert result.passed is True
        assert any("Pulled Ollama model" in a for a in result.actions_taken)
        # Also pulls the embedding model since list_models returns empty
        assert instance.pull_model.call_count == 2
        instance.pull_model.assert_any_call("llama3.2:1b")
        instance.pull_model.assert_any_call("all-minilm:l6-v2")

    @pytest.mark.asyncio
    async def test_model_missing_pull_fails(self, base_config):
        result = PreflightResult()
        with (
            patch("smollama.preflight.OllamaClient") as MockClient,
            patch("smollama.preflight.shutil.which", return_value=None),
        ):
            instance = MockClient.return_value
            instance.check_connection = AsyncMock(return_value=True)
            instance.list_models = AsyncMock(return_value=[])
            instance.pull_model = AsyncMock(return_value=False)
            await _check_ollama(base_config, result)

        assert result.passed is False
        assert any("not available" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_embedding_model_missing_is_warning(self, base_config):
        result = PreflightResult()
        with (
            patch("smollama.preflight.OllamaClient") as MockClient,
            patch("smollama.preflight.shutil.which", return_value=None),
        ):
            instance = MockClient.return_value
            instance.check_connection = AsyncMock(return_value=True)
            # Main model available, embedding model not
            instance.list_models = AsyncMock(return_value=["llama3.2:1b"])
            instance.pull_model = AsyncMock(return_value=False)
            await _check_ollama(base_config, result)

        assert result.passed is True  # Embedding model failure is warning only
        assert any("Embedding model" in w for w in result.warnings)


class TestPreflightMQTT:
    @pytest.mark.asyncio
    async def test_mqtt_reachable(self, base_config):
        result = PreflightResult()
        with patch("smollama.preflight.MQTTClient") as MockClient:
            instance = MockClient.return_value
            instance.check_connection = AsyncMock(return_value=True)
            await _check_mqtt(base_config, result)

        assert result.passed is True
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_mqtt_unreachable_no_mosquitto(self, base_config):
        result = PreflightResult()
        with (
            patch("smollama.preflight.MQTTClient") as MockClient,
            patch("smollama.preflight.shutil.which", return_value=None),
        ):
            instance = MockClient.return_value
            instance.check_connection = AsyncMock(return_value=False)
            await _check_mqtt(base_config, result)

        assert result.passed is False
        assert any("not reachable" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_mqtt_auto_start_mosquitto(self, base_config):
        result = PreflightResult()
        call_count = 0

        async def check_side_effect():
            nonlocal call_count
            call_count += 1
            # First call fails, subsequent calls succeed (mosquitto started)
            return call_count > 1

        with (
            patch("smollama.preflight.MQTTClient") as MockClient,
            patch("smollama.preflight.shutil.which", return_value="/usr/bin/mosquitto"),
            patch("smollama.preflight.subprocess.Popen") as mock_popen,
            patch("smollama.preflight.asyncio.sleep", new_callable=AsyncMock),
        ):
            instance = MockClient.return_value
            instance.check_connection = AsyncMock(side_effect=check_side_effect)
            await _check_mqtt(base_config, result)

        assert result.passed is True
        assert any("Started mosquitto" in a for a in result.actions_taken)
        mock_popen.assert_called_once()

    @pytest.mark.asyncio
    async def test_mqtt_auto_start_fails(self, base_config):
        result = PreflightResult()
        with (
            patch("smollama.preflight.MQTTClient") as MockClient,
            patch("smollama.preflight.shutil.which", return_value="/usr/bin/mosquitto"),
            patch("smollama.preflight.subprocess.Popen"),
            patch("smollama.preflight.asyncio.sleep", new_callable=AsyncMock),
        ):
            instance = MockClient.return_value
            instance.check_connection = AsyncMock(return_value=False)
            await _check_mqtt(base_config, result)

        assert result.passed is False
        assert any("not reachable" in e for e in result.errors)


class TestPreflightMem0:
    @pytest.mark.asyncio
    async def test_mem0_disabled_skipped(self, base_config):
        result = PreflightResult()
        await _check_mem0(base_config, result)
        assert result.passed is True
        assert result.warnings == []
        assert result.actions_taken == []

    @pytest.mark.asyncio
    async def test_mem0_healthy(self, base_config):
        base_config.mem0.enabled = True
        result = PreflightResult()
        mock_client = AsyncMock()
        mock_client.health_check = AsyncMock(return_value=True)
        mock_client.close = AsyncMock()
        with patch("smollama.mem0.Mem0Client", return_value=mock_client):
            await _check_mem0(base_config, result)

        assert result.passed is True
        assert result.warnings == []

    @pytest.mark.asyncio
    async def test_mem0_unhealthy_docker_auto_start(self, base_config, tmp_path):
        base_config.mem0.enabled = True
        compose_file = tmp_path / "docker-compose.yml"
        compose_file.write_text("version: '3'\n")
        base_config.mem0.compose_file = str(compose_file)
        result = PreflightResult()
        health_call_count = 0

        async def health_side_effect():
            nonlocal health_call_count
            health_call_count += 1
            return health_call_count > 2  # Unhealthy first, then healthy after docker start

        mock_client = AsyncMock()
        mock_client.health_check = AsyncMock(side_effect=health_side_effect)
        mock_client.close = AsyncMock()

        with (
            patch("smollama.mem0.Mem0Client", return_value=mock_client),
            patch("smollama.preflight.shutil.which", return_value="/usr/bin/docker"),
            patch("smollama.preflight.subprocess.run") as mock_run,
            patch("smollama.preflight.asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            await _check_mem0(base_config, result)

        assert result.passed is True  # mem0 is always warning-only
        assert any("Started Mem0" in a for a in result.actions_taken)

    @pytest.mark.asyncio
    async def test_mem0_unhealthy_no_docker(self, base_config):
        base_config.mem0.enabled = True
        result = PreflightResult()
        mock_client = AsyncMock()
        mock_client.health_check = AsyncMock(return_value=False)
        mock_client.close = AsyncMock()
        with (
            patch("smollama.mem0.Mem0Client", return_value=mock_client),
            patch("smollama.preflight.shutil.which", return_value=None),
        ):
            await _check_mem0(base_config, result)

        assert result.passed is True  # mem0 is warning only
        assert any("Docker is not installed" in w for w in result.warnings)


class TestPreflightSync:
    @pytest.mark.asyncio
    async def test_sync_disabled_skipped(self, base_config):
        result = PreflightResult()
        await _check_sync(base_config, result)
        assert result.warnings == []

    @pytest.mark.asyncio
    async def test_sync_no_llama_url_no_discovery(self, base_config):
        base_config.sync.enabled = True
        base_config.sync.llama_url = ""
        base_config.discovery.enabled = False
        result = PreflightResult()
        await _check_sync(base_config, result)
        assert len(result.warnings) == 1
        assert "llama_url" in result.warnings[0]


class TestRunPreflight:
    @pytest.mark.asyncio
    async def test_all_healthy(self, base_config):
        with (
            patch("smollama.preflight.OllamaClient") as MockOllama,
            patch("smollama.preflight.MQTTClient") as MockMQTT,
        ):
            ollama = MockOllama.return_value
            ollama.check_connection = AsyncMock(return_value=True)
            ollama.list_models = AsyncMock(return_value=["llama3.2:1b", "all-minilm:l6-v2"])

            mqtt = MockMQTT.return_value
            mqtt.check_connection = AsyncMock(return_value=True)

            result = await run_preflight(base_config)

        assert result.passed is True
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_ollama_failure_stops_pass(self, base_config):
        with (
            patch("smollama.preflight.OllamaClient") as MockOllama,
            patch("smollama.preflight.MQTTClient") as MockMQTT,
        ):
            ollama = MockOllama.return_value
            ollama.check_connection = AsyncMock(return_value=False)

            mqtt = MockMQTT.return_value
            mqtt.check_connection = AsyncMock(return_value=True)

            result = await run_preflight(base_config)

        assert result.passed is False
        assert len(result.errors) >= 1
