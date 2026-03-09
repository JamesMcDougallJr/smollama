"""Preflight checks for Smollama — validates and auto-starts services."""

import asyncio
import logging
import shutil
import socket
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .ollama_client import OllamaClient
from .mqtt_client import MQTTClient

logger = logging.getLogger(__name__)


@dataclass
class PreflightResult:
    """Result of preflight checks."""

    passed: bool = True
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    actions_taken: list[str] = field(default_factory=list)


async def _check_ollama(config: Config, result: PreflightResult) -> None:
    """Check Ollama connectivity and model availability."""
    client = OllamaClient(config.ollama)

    # Check connection
    connected = await client.check_connection()
    if not connected:
        result.errors.append(
            f"Ollama is not reachable at {config.ollama.base_url}. "
            "Make sure Ollama is running (https://ollama.com)."
        )
        result.passed = False
        return

    # Check if configured model is available
    models = await client.list_models()
    model = config.ollama.model
    base_model = model.split(":")[0]
    model_available = any(base_model in m for m in models)

    if not model_available:
        # Try auto-pull via CLI first, fall back to library
        pulled = False
        if shutil.which("ollama"):
            logger.info(f"Model '{model}' not found, pulling via ollama CLI...")
            try:
                proc = subprocess.run(
                    ["ollama", "pull", model],
                    capture_output=False,
                    timeout=600,
                )
                pulled = proc.returncode == 0
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

        if not pulled:
            logger.info(f"Model '{model}' not found, pulling via ollama library...")
            pulled = await client.pull_model(model)

        if pulled:
            result.actions_taken.append(f"Pulled Ollama model '{model}'")
        else:
            result.errors.append(
                f"Ollama model '{model}' is not available and could not be pulled. "
                f"Run: ollama pull {model}"
            )
            result.passed = False

    # Check embedding model if memory uses ollama
    if config.memory.embedding_provider == "ollama":
        emb_model = config.memory.embedding_model
        emb_base = emb_model.split(":")[0]
        emb_available = any(emb_base in m for m in models)

        if not emb_available:
            pulled = False
            if shutil.which("ollama"):
                try:
                    proc = subprocess.run(
                        ["ollama", "pull", emb_model],
                        capture_output=False,
                        timeout=600,
                    )
                    pulled = proc.returncode == 0
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    pass

            if not pulled:
                pulled = await client.pull_model(emb_model)

            if pulled:
                result.actions_taken.append(f"Pulled embedding model '{emb_model}'")
            else:
                result.warnings.append(
                    f"Embedding model '{emb_model}' is not available. "
                    f"Memory features may not work. Run: ollama pull {emb_model}"
                )


async def _check_mqtt(config: Config, result: PreflightResult) -> None:
    """Check MQTT broker connectivity, auto-start if possible."""
    client = MQTTClient(config.mqtt)
    reachable = await client.check_connection()

    if not reachable:
        # Try auto-start mosquitto
        mosquitto_bin = shutil.which("mosquitto")
        if mosquitto_bin:
            logger.info("MQTT broker not reachable, attempting to start mosquitto...")
            try:
                subprocess.Popen(
                    ["mosquitto", "-d", "-p", str(config.mqtt.port)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError as e:
                logger.debug(f"Failed to start mosquitto: {e}")

            # Retry connection for up to 5 seconds
            for _ in range(10):
                await asyncio.sleep(0.5)
                reachable = await client.check_connection()
                if reachable:
                    result.actions_taken.append(
                        f"Started mosquitto on port {config.mqtt.port}"
                    )
                    break

        if not reachable:
            result.errors.append(
                f"MQTT broker is not reachable at {config.mqtt.broker}:{config.mqtt.port}. "
                "Install and start mosquitto: "
                "brew install mosquitto && brew services start mosquitto (macOS) or "
                "sudo apt install mosquitto && sudo systemctl start mosquitto (Linux)."
            )
            result.passed = False
            return

    # Network binding check for master nodes
    # If broker is localhost and we subscribe to other nodes' topics,
    # check if mosquitto is listening on 0.0.0.0
    broker = config.mqtt.broker
    if broker in ("localhost", "127.0.0.1") and config.mqtt.topics.subscribe:
        # Check if any subscribe topic references other nodes
        node_name = config.node.name
        has_external_topics = any(
            not topic.startswith(f"smollama/{node_name}")
            and topic != "smollama/broadcast"
            for topic in config.mqtt.topics.subscribe
        )

        if has_external_topics:
            # Test if broker is accessible on LAN IP
            try:
                lan_ip = _get_lan_ip()
                if lan_ip and lan_ip not in ("127.0.0.1", "::1"):
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(1)
                    try:
                        sock.connect((lan_ip, config.mqtt.port))
                        sock.close()
                    except (socket.timeout, ConnectionRefusedError, OSError):
                        result.warnings.append(
                            "MQTT broker is only listening on localhost. "
                            "Other nodes won't be able to connect. "
                            "Add 'listener 1883 0.0.0.0' to your mosquitto.conf."
                        )
            except Exception:
                pass  # Can't determine LAN IP, skip check


def _get_lan_ip() -> str | None:
    """Get the machine's LAN IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.1)
        # Doesn't actually send data — just determines the outgoing interface
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


async def _check_mem0(config: Config, result: PreflightResult) -> None:
    """Check Mem0 services, auto-start via Docker if possible."""
    if not config.mem0.enabled:
        return

    try:
        from .mem0 import Mem0Client
    except ImportError:
        result.warnings.append(
            "Mem0 is enabled but mem0 dependencies are not installed. "
            "Install with: uv sync --extra mem0"
        )
        return

    client = Mem0Client(config.mem0.server_url)
    try:
        healthy = await client.health_check()
    finally:
        await client.close()

    if healthy:
        return

    # Try auto-start via docker compose
    if not shutil.which("docker"):
        result.warnings.append(
            f"Mem0 server is not reachable at {config.mem0.server_url} "
            "and Docker is not installed. Mem0 features will be unavailable."
        )
        return

    compose_path = Path(config.mem0.compose_file)
    if not compose_path.is_absolute():
        compose_path = Path.cwd() / compose_path

    if not compose_path.exists():
        result.warnings.append(
            f"Mem0 server is not reachable and compose file not found at {compose_path}. "
            "Mem0 features will be unavailable."
        )
        return

    logger.info("Mem0 not reachable, starting via docker compose...")
    try:
        subprocess.run(
            ["docker", "compose", "-f", str(compose_path), "up", "-d"],
            capture_output=True,
            timeout=60,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        result.warnings.append(f"Failed to start Mem0 via Docker: {e}")
        return

    # Wait up to 30s for mem0 to become healthy
    client = Mem0Client(config.mem0.server_url)
    try:
        for _ in range(6):
            await asyncio.sleep(5)
            if await client.health_check():
                result.actions_taken.append("Started Mem0 services via Docker Compose")
                return
    finally:
        await client.close()

    result.warnings.append(
        "Started Mem0 Docker containers but server did not become healthy within 30s. "
        "Check with: smollama mem0 status"
    )


async def _check_sync(config: Config, result: PreflightResult) -> None:
    """Informational check for sync configuration."""
    if not config.sync.enabled:
        return

    if not config.sync.llama_url and not config.discovery.enabled:
        result.warnings.append(
            "Sync is enabled but no llama_url is configured and discovery is disabled. "
            "Set sync.llama_url or enable discovery for cross-node sync."
        )


async def run_preflight(config: Config) -> PreflightResult:
    """Run all preflight checks.

    Args:
        config: Loaded Smollama configuration.

    Returns:
        PreflightResult with pass/fail status, warnings, errors, and actions taken.
    """
    result = PreflightResult()

    await _check_ollama(config, result)
    await _check_mqtt(config, result)
    await _check_mem0(config, result)
    await _check_sync(config, result)

    return result
