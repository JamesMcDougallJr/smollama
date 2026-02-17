"""Configuration loading for Smollama."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class NodeConfig:
    name: str = "smollama-node"


@dataclass
class OllamaConfig:
    host: str = "localhost"
    port: int = 11434
    model: str = "llama3.2:1b"

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


@dataclass
class MQTTTopicsConfig:
    subscribe: list[str] = field(default_factory=list)
    publish_prefix: str = "smollama"


@dataclass
class MQTTConfig:
    broker: str = "localhost"
    port: int = 1883
    topics: MQTTTopicsConfig = field(default_factory=MQTTTopicsConfig)
    username: str | None = None
    password: str | None = None


@dataclass
class GPIOPinConfig:
    pin: int
    name: str
    mode: str = "input"


@dataclass
class GPIOConfig:
    pins: list[GPIOPinConfig] = field(default_factory=list)
    mock: bool = False


@dataclass
class AgentConfig:
    system_prompt: str = (
        "You are a home automation assistant running on a Raspberry Pi. "
        "You can read GPIO sensors and communicate with other nodes via MQTT."
    )
    max_tool_iterations: int = 10
    ollama_retry_attempts: int = 3
    ollama_retry_backoff_seconds: float = 2.0
    ollama_fallback_mode: str = "skip"  # "skip" or "queue"


@dataclass
class MemoryConfig:
    """Configuration for the memory system."""

    db_path: str = "~/.smollama/memory.db"
    embedding_provider: str = "ollama"
    embedding_model: str = "all-minilm:l6-v2"
    observation_enabled: bool = True
    observation_interval_minutes: int = 15
    observation_lookback_minutes: int = 60
    sensor_log_retention_days: int = 90


@dataclass
class SyncConfig:
    """Configuration for CRDT sync infrastructure."""

    enabled: bool = True
    llama_url: str = ""  # URL of the main Llama node
    sync_interval_minutes: int = 5
    retry_max_attempts: int = 3
    batch_size: int = 100
    crdt_db_path: str = "~/.smollama/sync.db"


@dataclass
class Mem0Config:
    """Configuration for Mem0 semantic memory layer.

    Mem0 provides cross-node semantic search on the Llama (master) node.
    The bridge indexes observations and memories from the CRDT log.
    """

    enabled: bool = False
    server_url: str = "http://localhost:8050"
    bridge_enabled: bool = False  # Only true on Llama node
    index_observations: bool = True
    index_memories: bool = True
    bridge_interval_seconds: int = 30
    compose_file: str = "deploy/mem0/docker-compose.yml"


@dataclass
class DiscoveryConfig:
    """Configuration for mDNS/Zeroconf service discovery."""

    enabled: bool = True
    service_type: str = "_smollama._tcp"
    announce: bool = True  # Announce this node
    browse: bool = True  # Browse for other nodes
    cache_ttl_seconds: int = 300  # 5 minutes
    discovery_timeout_seconds: int = 10  # Wait up to 10s on startup


@dataclass
class BuiltinPluginConfig:
    """Configuration for a builtin plugin (GPIO, System)."""

    enabled: bool = True
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class CustomPluginConfig:
    """Configuration for a custom plugin."""

    name: str
    enabled: bool = True
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class PluginsConfig:
    """Configuration for the plugin system."""

    paths: list[str] = field(default_factory=list)
    """Additional directories to scan for plugins"""

    builtin: dict[str, BuiltinPluginConfig] = field(default_factory=dict)
    """Builtin plugin configurations (gpio, system)"""

    custom: list[CustomPluginConfig] = field(default_factory=list)
    """Custom plugin configurations"""


@dataclass
class Config:
    node: NodeConfig = field(default_factory=NodeConfig)
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    mqtt: MQTTConfig = field(default_factory=MQTTConfig)
    gpio: GPIOConfig = field(default_factory=GPIOConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    sync: SyncConfig = field(default_factory=SyncConfig)
    mem0: Mem0Config = field(default_factory=Mem0Config)
    discovery: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    plugins: PluginsConfig = field(default_factory=PluginsConfig)


def _get_env(key: str, default: Any = None) -> Any:
    """Get environment variable with SMOLLAMA_ prefix."""
    return os.environ.get(f"SMOLLAMA_{key}", default)


def _apply_env_overrides(config: Config) -> Config:
    """Apply environment variable overrides to config."""
    # Node overrides
    if name := _get_env("NODE_NAME"):
        config.node.name = name

    # Ollama overrides
    if host := _get_env("OLLAMA_HOST"):
        config.ollama.host = host
    if port := _get_env("OLLAMA_PORT"):
        config.ollama.port = int(port)
    if model := _get_env("OLLAMA_MODEL"):
        config.ollama.model = model

    # MQTT overrides
    if broker := _get_env("MQTT_BROKER"):
        config.mqtt.broker = broker
    if port := _get_env("MQTT_PORT"):
        config.mqtt.port = int(port)
    if username := _get_env("MQTT_USERNAME"):
        config.mqtt.username = username
    if password := _get_env("MQTT_PASSWORD"):
        config.mqtt.password = password

    # GPIO mock mode
    if mock := _get_env("GPIO_MOCK"):
        config.gpio.mock = mock.lower() in ("true", "1", "yes")

    # Memory overrides
    if db_path := _get_env("MEMORY_DB_PATH"):
        config.memory.db_path = db_path
    if embedding_provider := _get_env("MEMORY_EMBEDDING_PROVIDER"):
        config.memory.embedding_provider = embedding_provider
    if embedding_model := _get_env("MEMORY_EMBEDDING_MODEL"):
        config.memory.embedding_model = embedding_model
    if obs_enabled := _get_env("MEMORY_OBSERVATION_ENABLED"):
        config.memory.observation_enabled = obs_enabled.lower() in ("true", "1", "yes")
    if obs_interval := _get_env("MEMORY_OBSERVATION_INTERVAL"):
        config.memory.observation_interval_minutes = int(obs_interval)

    # Sync overrides
    if sync_enabled := _get_env("SYNC_ENABLED"):
        config.sync.enabled = sync_enabled.lower() in ("true", "1", "yes")
    if llama_url := _get_env("SYNC_LLAMA_URL"):
        config.sync.llama_url = llama_url
    if sync_interval := _get_env("SYNC_INTERVAL"):
        config.sync.sync_interval_minutes = int(sync_interval)

    # Mem0 overrides
    if mem0_enabled := _get_env("MEM0_ENABLED"):
        config.mem0.enabled = mem0_enabled.lower() in ("true", "1", "yes")
    if mem0_url := _get_env("MEM0_SERVER_URL"):
        config.mem0.server_url = mem0_url
    if mem0_bridge := _get_env("MEM0_BRIDGE_ENABLED"):
        config.mem0.bridge_enabled = mem0_bridge.lower() in ("true", "1", "yes")

    # Discovery overrides
    if discovery_enabled := _get_env("DISCOVERY_ENABLED"):
        config.discovery.enabled = discovery_enabled.lower() in ("true", "1", "yes")
    if discovery_announce := _get_env("DISCOVERY_ANNOUNCE"):
        config.discovery.announce = discovery_announce.lower() in ("true", "1", "yes")
    if discovery_browse := _get_env("DISCOVERY_BROWSE"):
        config.discovery.browse = discovery_browse.lower() in ("true", "1", "yes")

    return config


def _parse_mqtt_topics(data: dict) -> MQTTTopicsConfig:
    """Parse MQTT topics configuration."""
    return MQTTTopicsConfig(
        subscribe=data.get("subscribe", []),
        publish_prefix=data.get("publish_prefix", "smollama"),
    )


def _parse_gpio_pins(data: list) -> list[GPIOPinConfig]:
    """Parse GPIO pin configurations."""
    pins = []
    for pin_data in data:
        pins.append(
            GPIOPinConfig(
                pin=pin_data["pin"],
                name=pin_data["name"],
                mode=pin_data.get("mode", "input"),
            )
        )
    return pins


def load_config(config_path: str | Path | None = None) -> Config:
    """Load configuration from YAML file with environment variable overrides.

    Args:
        config_path: Path to YAML config file. If None, uses default config.

    Returns:
        Loaded and validated Config object.
    """
    config = Config()

    if config_path:
        path = Path(config_path)
        if path.exists():
            with open(path) as f:
                data = yaml.safe_load(f) or {}

            # Parse node config
            if "node" in data:
                config.node = NodeConfig(
                    name=data["node"].get("name", config.node.name)
                )

            # Parse ollama config
            if "ollama" in data:
                ollama_data = data["ollama"]
                config.ollama = OllamaConfig(
                    host=ollama_data.get("host", config.ollama.host),
                    port=ollama_data.get("port", config.ollama.port),
                    model=ollama_data.get("model", config.ollama.model),
                )

            # Parse MQTT config
            if "mqtt" in data:
                mqtt_data = data["mqtt"]
                topics = MQTTTopicsConfig()
                if "topics" in mqtt_data:
                    topics = _parse_mqtt_topics(mqtt_data["topics"])

                config.mqtt = MQTTConfig(
                    broker=mqtt_data.get("broker", config.mqtt.broker),
                    port=mqtt_data.get("port", config.mqtt.port),
                    topics=topics,
                    username=mqtt_data.get("username"),
                    password=mqtt_data.get("password"),
                )

            # Parse GPIO config
            if "gpio" in data:
                gpio_data = data["gpio"]
                pins = []
                if "pins" in gpio_data:
                    pins = _parse_gpio_pins(gpio_data["pins"])

                config.gpio = GPIOConfig(
                    pins=pins,
                    mock=gpio_data.get("mock", False),
                )

            # Parse agent config
            if "agent" in data:
                agent_data = data["agent"]
                config.agent = AgentConfig(
                    system_prompt=agent_data.get(
                        "system_prompt", config.agent.system_prompt
                    ),
                    max_tool_iterations=agent_data.get(
                        "max_tool_iterations", config.agent.max_tool_iterations
                    ),
                    ollama_retry_attempts=agent_data.get(
                        "ollama_retry_attempts", config.agent.ollama_retry_attempts
                    ),
                    ollama_retry_backoff_seconds=agent_data.get(
                        "ollama_retry_backoff_seconds", config.agent.ollama_retry_backoff_seconds
                    ),
                    ollama_fallback_mode=agent_data.get(
                        "ollama_fallback_mode", config.agent.ollama_fallback_mode
                    ),
                )

            # Parse memory config
            if "memory" in data:
                mem_data = data["memory"]
                config.memory = MemoryConfig(
                    db_path=mem_data.get("db_path", config.memory.db_path),
                    embedding_provider=mem_data.get(
                        "embedding_provider", config.memory.embedding_provider
                    ),
                    embedding_model=mem_data.get(
                        "embedding_model", config.memory.embedding_model
                    ),
                    observation_enabled=mem_data.get(
                        "observation_enabled", config.memory.observation_enabled
                    ),
                    observation_interval_minutes=mem_data.get(
                        "observation_interval_minutes",
                        config.memory.observation_interval_minutes,
                    ),
                    observation_lookback_minutes=mem_data.get(
                        "observation_lookback_minutes",
                        config.memory.observation_lookback_minutes,
                    ),
                    sensor_log_retention_days=mem_data.get(
                        "sensor_log_retention_days",
                        config.memory.sensor_log_retention_days,
                    ),
                )

            # Parse sync config
            if "sync" in data:
                sync_data = data["sync"]
                config.sync = SyncConfig(
                    enabled=sync_data.get("enabled", config.sync.enabled),
                    llama_url=sync_data.get("llama_url", config.sync.llama_url),
                    sync_interval_minutes=sync_data.get(
                        "sync_interval_minutes", config.sync.sync_interval_minutes
                    ),
                    retry_max_attempts=sync_data.get(
                        "retry_max_attempts", config.sync.retry_max_attempts
                    ),
                    batch_size=sync_data.get("batch_size", config.sync.batch_size),
                    crdt_db_path=sync_data.get(
                        "crdt_db_path", config.sync.crdt_db_path
                    ),
                )

            # Parse mem0 config
            if "mem0" in data:
                mem0_data = data["mem0"]
                config.mem0 = Mem0Config(
                    enabled=mem0_data.get("enabled", config.mem0.enabled),
                    server_url=mem0_data.get("server_url", config.mem0.server_url),
                    bridge_enabled=mem0_data.get(
                        "bridge_enabled", config.mem0.bridge_enabled
                    ),
                    index_observations=mem0_data.get(
                        "index_observations", config.mem0.index_observations
                    ),
                    index_memories=mem0_data.get(
                        "index_memories", config.mem0.index_memories
                    ),
                    bridge_interval_seconds=mem0_data.get(
                        "bridge_interval_seconds", config.mem0.bridge_interval_seconds
                    ),
                    compose_file=mem0_data.get(
                        "compose_file", config.mem0.compose_file
                    ),
                )

            # Parse discovery config
            if "discovery" in data:
                disc_data = data["discovery"]
                config.discovery = DiscoveryConfig(
                    enabled=disc_data.get("enabled", config.discovery.enabled),
                    service_type=disc_data.get("service_type", config.discovery.service_type),
                    announce=disc_data.get("announce", config.discovery.announce),
                    browse=disc_data.get("browse", config.discovery.browse),
                    cache_ttl_seconds=disc_data.get(
                        "cache_ttl_seconds", config.discovery.cache_ttl_seconds
                    ),
                    discovery_timeout_seconds=disc_data.get(
                        "discovery_timeout_seconds", config.discovery.discovery_timeout_seconds
                    ),
                )

            # Parse plugins config
            if "plugins" in data:
                plugins_data = data["plugins"]
                builtin_plugins = {}
                custom_plugins = []

                # Parse builtin plugins
                if "builtin" in plugins_data:
                    for plugin_name, plugin_data in plugins_data["builtin"].items():
                        builtin_plugins[plugin_name] = BuiltinPluginConfig(
                            enabled=plugin_data.get("enabled", True),
                            config=plugin_data.get("config", {}),
                        )

                # Parse custom plugins
                if "custom" in plugins_data:
                    for plugin_data in plugins_data["custom"]:
                        custom_plugins.append(
                            CustomPluginConfig(
                                name=plugin_data["name"],
                                enabled=plugin_data.get("enabled", True),
                                config=plugin_data.get("config", {}),
                            )
                        )

                config.plugins = PluginsConfig(
                    paths=plugins_data.get("paths", []),
                    builtin=builtin_plugins,
                    custom=custom_plugins,
                )
            else:
                # Backward compatibility: if no plugins config, auto-enable builtins
                # with config from legacy gpio section
                config.plugins = PluginsConfig(
                    paths=[],
                    builtin={
                        "gpio": BuiltinPluginConfig(
                            enabled=True,
                            config={"mock": config.gpio.mock, "pins": []},
                        ),
                        "system": BuiltinPluginConfig(enabled=True, config={}),
                    },
                    custom=[],
                )

    # Apply environment variable overrides
    config = _apply_env_overrides(config)

    # Set default subscribe topics if none configured
    if not config.mqtt.topics.subscribe:
        config.mqtt.topics.subscribe = [
            "smollama/broadcast",
            f"smollama/{config.node.name}/#",
        ]

    # Set default publish prefix
    if config.mqtt.topics.publish_prefix == "smollama":
        config.mqtt.topics.publish_prefix = f"smollama/{config.node.name}"

    return config
