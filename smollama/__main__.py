"""CLI entry point for Smollama."""

import argparse
import asyncio
import logging
import subprocess
import sys
from pathlib import Path

from .config import load_config
from .agent import run_agent
from .ollama_client import OllamaClient
from .mqtt_client import MQTTClient


def setup_logging(verbose: bool = False) -> None:
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


async def cmd_run(args: argparse.Namespace) -> int:
    """Run the agent."""
    config = load_config(args.config)

    print(f"Starting Smollama node: {config.node.name}")
    print(f"Ollama: {config.ollama.base_url} (model: {config.ollama.model})")
    print(f"MQTT: {config.mqtt.broker}:{config.mqtt.port}")
    print(f"GPIO: {len(config.gpio.pins)} pins configured", end="")
    if config.gpio.mock:
        print(" (mock mode)")
    else:
        print()

    try:
        await run_agent(config)
    except KeyboardInterrupt:
        print("\nShutting down...")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    return 0


async def cmd_dashboard(args: argparse.Namespace) -> int:
    """Start the web dashboard."""
    config = load_config(args.config)

    try:
        from .dashboard import create_app
        from .memory import LocalStore, MockEmbeddings, OllamaEmbeddings
        from .readings import ReadingManager, SystemReadingProvider

        import uvicorn
    except ImportError as e:
        print(f"Dashboard dependencies not installed: {e}", file=sys.stderr)
        print("Install with: pip install smollama[dashboard]", file=sys.stderr)
        return 1

    # Initialize memory store
    if config.memory.embedding_provider == "ollama":
        embeddings = OllamaEmbeddings(
            model=config.memory.embedding_model,
            host=config.ollama.base_url,
        )
    else:
        embeddings = MockEmbeddings()

    store = LocalStore(
        db_path=config.memory.db_path,
        node_id=config.node.name,
        embeddings=embeddings,
    )
    store.connect()

    # Initialize readings manager
    readings = ReadingManager()
    readings.register(SystemReadingProvider())

    # Optionally add GPIO if configured
    if config.gpio.pins:
        from .gpio_reader import GPIOReader
        from .readings import GPIOReadingProvider

        gpio = GPIOReader(config.gpio)
        readings.register(GPIOReadingProvider(gpio))

    print(f"Starting Smollama Dashboard")
    print(f"Node: {config.node.name}")
    print(f"URL: http://0.0.0.0:{args.port}")

    app = create_app(config, store=store, readings=readings)

    try:
        verbose = getattr(args, "verbose", False)
        config_uvicorn = uvicorn.Config(
            app,
            host="0.0.0.0",
            port=args.port,
            log_level="info" if verbose else "warning",
        )
        server = uvicorn.Server(config_uvicorn)
        await server.serve()
    finally:
        store.close()

    return 0


async def cmd_status(args: argparse.Namespace) -> int:
    """Check connectivity status."""
    config = load_config(args.config)

    print(f"Smollama Status Check")
    print(f"=====================")
    print(f"Node: {config.node.name}")
    print()

    # Check Ollama
    print(f"Ollama ({config.ollama.base_url}):")
    ollama = OllamaClient(config.ollama)
    if await ollama.check_connection():
        models = await ollama.list_models()
        print(f"  Status: Connected")
        print(f"  Configured model: {config.ollama.model}")
        print(f"  Available models: {', '.join(models) if models else 'none'}")

        # Check if configured model is available
        base_model = config.ollama.model.split(":")[0]
        if any(base_model in m for m in models):
            print(f"  Model available: Yes")
        else:
            print(f"  Model available: No (run 'ollama pull {config.ollama.model}')")
    else:
        print(f"  Status: Not connected")
        print(f"  Make sure Ollama is running")

    print()

    # Check MQTT
    print(f"MQTT ({config.mqtt.broker}:{config.mqtt.port}):")
    mqtt = MQTTClient(config.mqtt)
    if await mqtt.check_connection():
        print(f"  Status: Reachable")
        print(f"  Subscribe topics: {', '.join(config.mqtt.topics.subscribe)}")
        print(f"  Publish prefix: {config.mqtt.topics.publish_prefix}")
    else:
        print(f"  Status: Not reachable")
        print(f"  Make sure the MQTT broker is running")

    print()

    # GPIO info
    print(f"GPIO:")
    print(f"  Mode: {'Mock' if config.gpio.mock else 'Real'}")
    print(f"  Configured pins: {len(config.gpio.pins)}")
    for pin in config.gpio.pins:
        print(f"    - {pin.name} (pin {pin.pin}, {pin.mode})")

    return 0


def _get_compose_path(config_path: Path | None) -> Path:
    """Get the docker-compose.yml path for mem0.

    Args:
        config_path: Path to smollama config file.

    Returns:
        Path to docker-compose.yml.
    """
    config = load_config(config_path)

    # Compose file path is relative to the config file or cwd
    compose_path = Path(config.mem0.compose_file)

    if not compose_path.is_absolute():
        if config_path:
            compose_path = config_path.parent / compose_path
        else:
            compose_path = Path.cwd() / compose_path

    return compose_path


def cmd_mem0_start(args: argparse.Namespace) -> int:
    """Start mem0 services via Docker Compose."""
    compose_path = _get_compose_path(args.config)

    if not compose_path.exists():
        print(f"Error: Docker Compose file not found: {compose_path}", file=sys.stderr)
        print("Make sure you're in the smollama directory or provide --config", file=sys.stderr)
        return 1

    print(f"Starting Mem0 services...")
    print(f"Using: {compose_path}")

    result = subprocess.run(
        ["docker", "compose", "-f", str(compose_path), "up", "-d"],
        capture_output=False,
    )

    if result.returncode == 0:
        print()
        print("Mem0 services started successfully!")
        print("  - Qdrant: http://localhost:6333")
        print("  - Mem0:   http://localhost:8050")
        print()
        print("Check status with: smollama mem0 status")
    else:
        print("Failed to start Mem0 services", file=sys.stderr)

    return result.returncode


def cmd_mem0_stop(args: argparse.Namespace) -> int:
    """Stop mem0 services."""
    compose_path = _get_compose_path(args.config)

    if not compose_path.exists():
        print(f"Error: Docker Compose file not found: {compose_path}", file=sys.stderr)
        return 1

    print("Stopping Mem0 services...")

    result = subprocess.run(
        ["docker", "compose", "-f", str(compose_path), "down"],
        capture_output=False,
    )

    return result.returncode


async def cmd_mem0_status(args: argparse.Namespace) -> int:
    """Check mem0 services status."""
    config = load_config(args.config)
    compose_path = _get_compose_path(args.config)

    print("Mem0 Status Check")
    print("=================")
    print()

    # Check Docker containers
    print("Docker Containers:")
    if compose_path.exists():
        result = subprocess.run(
            ["docker", "compose", "-f", str(compose_path), "ps", "--format", "table {{.Name}}\t{{.Status}}"],
            capture_output=True,
            text=True,
        )
        if result.stdout.strip():
            for line in result.stdout.strip().split("\n"):
                print(f"  {line}")
        else:
            print("  No containers running")
    else:
        print(f"  Compose file not found: {compose_path}")

    print()

    # Check Qdrant health
    print("Qdrant (localhost:6333):")
    try:
        import httpx

        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get("http://localhost:6333/health")
            if resp.status_code == 200:
                print("  Status: Healthy")
            else:
                print(f"  Status: Unhealthy (HTTP {resp.status_code})")
    except Exception as e:
        print(f"  Status: Not reachable ({e})")

    print()

    # Check Mem0 health
    print(f"Mem0 ({config.mem0.server_url}):")
    try:
        from .mem0 import Mem0Client

        client = Mem0Client(config.mem0.server_url)
        if await client.health_check():
            print("  Status: Healthy")
        else:
            print("  Status: Unhealthy")
        await client.close()
    except Exception as e:
        print(f"  Status: Not reachable ({e})")

    print()

    # Show config
    print("Configuration:")
    print(f"  Enabled: {config.mem0.enabled}")
    print(f"  Bridge enabled: {config.mem0.bridge_enabled}")
    print(f"  Server URL: {config.mem0.server_url}")

    return 0


def cmd_mem0_logs(args: argparse.Namespace) -> int:
    """Show mem0 service logs."""
    compose_path = _get_compose_path(args.config)

    if not compose_path.exists():
        print(f"Error: Docker Compose file not found: {compose_path}", file=sys.stderr)
        return 1

    cmd = ["docker", "compose", "-f", str(compose_path), "logs"]

    if args.follow:
        cmd.append("-f")

    if args.service:
        cmd.append(args.service)

    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        pass

    return 0


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="smollama",
        description="A distributed LLM coordination system for Raspberry Pi",
    )
    parser.add_argument(
        "-c", "--config",
        type=Path,
        default=None,
        help="Path to config file (default: config.yaml)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Run command
    run_parser = subparsers.add_parser("run", help="Start the agent")
    run_parser.set_defaults(func=cmd_run)

    # Status command
    status_parser = subparsers.add_parser("status", help="Check connectivity status")
    status_parser.set_defaults(func=cmd_status)

    # Dashboard command
    dashboard_parser = subparsers.add_parser("dashboard", help="Start the web dashboard")
    dashboard_parser.add_argument(
        "-p", "--port",
        type=int,
        default=8080,
        help="Port to run dashboard on (default: 8080)",
    )
    dashboard_parser.set_defaults(func=cmd_dashboard)

    # Mem0 commands
    mem0_parser = subparsers.add_parser("mem0", help="Manage Mem0 semantic memory services")
    mem0_subparsers = mem0_parser.add_subparsers(dest="mem0_command", help="Mem0 commands")

    # mem0 start
    mem0_start = mem0_subparsers.add_parser("start", help="Start Mem0 services")
    mem0_start.set_defaults(func=cmd_mem0_start)

    # mem0 stop
    mem0_stop = mem0_subparsers.add_parser("stop", help="Stop Mem0 services")
    mem0_stop.set_defaults(func=cmd_mem0_stop)

    # mem0 status
    mem0_status = mem0_subparsers.add_parser("status", help="Check Mem0 service status")
    mem0_status.set_defaults(func=cmd_mem0_status, is_async=True)

    # mem0 logs
    mem0_logs = mem0_subparsers.add_parser("logs", help="View Mem0 service logs")
    mem0_logs.add_argument(
        "-f", "--follow",
        action="store_true",
        help="Follow log output",
    )
    mem0_logs.add_argument(
        "service",
        nargs="?",
        choices=["qdrant", "mem0"],
        help="Service to show logs for (default: all)",
    )
    mem0_logs.set_defaults(func=cmd_mem0_logs)

    args = parser.parse_args()

    setup_logging(args.verbose)

    if not args.command:
        parser.print_help()
        return 1

    # Handle mem0 subcommand requiring its own subcommand
    if args.command == "mem0":
        if not args.mem0_command:
            mem0_parser.print_help()
            return 1

    # Check if function is async
    func = args.func
    is_async = getattr(args, "is_async", False) or asyncio.iscoroutinefunction(func)

    if is_async:
        return asyncio.run(func(args))
    else:
        return func(args)


if __name__ == "__main__":
    sys.exit(main())
