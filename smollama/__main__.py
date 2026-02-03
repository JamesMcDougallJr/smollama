"""CLI entry point for Smollama."""

import argparse
import asyncio
import logging
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

    args = parser.parse_args()

    setup_logging(args.verbose)

    if not args.command:
        parser.print_help()
        return 1

    return asyncio.run(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
