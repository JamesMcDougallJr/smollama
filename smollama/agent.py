"""Main agent loop for Smollama."""

import asyncio
import json
import logging
from typing import Any

from .config import Config
from .gpio_reader import GPIOReader
from .memory import LocalStore, MockEmbeddings, ObservationLoop, OllamaEmbeddings
from .mqtt_client import MQTTClient, Message
from .ollama_client import (
    OllamaClient,
    ChatResponse,
    format_tool_result,
    format_assistant_tool_calls,
)
from .readings import GPIOReadingProvider, ReadingManager, SystemReadingProvider
from .tools import ToolRegistry, PublishTool, GetRecentMessagesTool
from .tools.reading_tools import GetReadingHistoryTool, ListSourcesTool, ReadSourceTool
from .tools.memory_tools import ObserveTool, RecallTool, RememberTool
from .mem0 import Mem0Client, Mem0Bridge, CrossNodeRecallTool
from .sync import CRDTLog, SyncClient
from .discovery import DiscoveryManager

logger = logging.getLogger(__name__)


class Agent:
    """Main agent that coordinates LLM, MQTT, GPIO, and memory."""

    def __init__(self, config: Config):
        self.config = config
        self._running = False

        # Initialize core components
        self._ollama = OllamaClient(config.ollama)
        self._mqtt = MQTTClient(config.mqtt)
        self._gpio = GPIOReader(config.gpio)

        # Initialize unified reading manager
        self._readings = ReadingManager()
        self._readings.register(GPIOReadingProvider(self._gpio))
        self._readings.register(SystemReadingProvider())

        # Initialize memory system
        if config.memory.embedding_provider == "ollama":
            embedder = OllamaEmbeddings(
                model=config.memory.embedding_model,
                host=config.ollama.base_url,
            )
        else:
            embedder = MockEmbeddings()

        self._memory = LocalStore(
            db_path=config.memory.db_path,
            node_id=config.node.name,
            embeddings=embedder,
        )

        # Initialize observation loop (started later in start())
        self._observation_loop: ObservationLoop | None = None
        if config.memory.observation_enabled:
            self._observation_loop = ObservationLoop(
                store=self._memory,
                readings=self._readings,
                agent=self,
                interval_minutes=config.memory.observation_interval_minutes,
                lookback_minutes=config.memory.observation_lookback_minutes,
            )

        # Initialize Mem0 components (for Llama node cross-node search)
        self._mem0_client: Mem0Client | None = None
        self._mem0_bridge: Mem0Bridge | None = None
        if config.mem0.enabled:
            self._mem0_client = Mem0Client(config.mem0.server_url)
            if config.mem0.bridge_enabled:
                # Bridge requires CRDT log access
                crdt_log = CRDTLog(
                    db_path=config.sync.crdt_db_path,
                    node_id=config.node.name,
                )
                self._mem0_bridge = Mem0Bridge(config.mem0, crdt_log)

        # Initialize sync system if enabled
        self._sync_client: SyncClient | None = None
        self._stop_event = asyncio.Event()
        if config.sync.enabled:
            crdt_log = CRDTLog(
                db_path=config.sync.crdt_db_path,
                node_id=config.node.name,
            )
            crdt_log.connect()
            self._sync_client = SyncClient(
                crdt_log=crdt_log,
                remote_url=config.sync.llama_url if config.sync.llama_url else None,
                batch_size=config.sync.batch_size,
                max_retries=config.sync.retry_max_attempts,
            )

        # Initialize discovery if enabled
        # Note: Discovery requires dashboard to be running (for port announcement)
        # This is just initialized here but not started in agent mode
        self._discovery_manager: DiscoveryManager | None = None
        if config.discovery.enabled:
            # Determine node type for announcement
            node_type = "llama" if config.mem0.bridge_enabled else "alpaca"
            # Port: default to 8080 (dashboard port)
            # Note: Agent-only mode won't have a port to announce
            port = 8080  # TODO: Make configurable

            self._discovery_manager = DiscoveryManager(
                node_name=config.node.name,
                node_type=node_type,
                port=port,
                service_type=config.discovery.service_type,
                announce=config.discovery.announce,
                browse=config.discovery.browse,
                cache_ttl_seconds=config.discovery.cache_ttl_seconds,
            )

        # Initialize tool registry with unified reading tools
        self._tools = ToolRegistry()

        # Reading tools (unified interface)
        self._tools.register(ReadSourceTool(self._readings))
        self._tools.register(ListSourcesTool(self._readings))
        self._tools.register(GetReadingHistoryTool(self._memory))

        # Memory tools
        self._tools.register(RecallTool(self._memory))
        self._tools.register(RememberTool(self._memory))
        self._tools.register(ObserveTool(self._memory))

        # MQTT tools
        self._tools.register(PublishTool(self._mqtt))
        self._tools.register(GetRecentMessagesTool(self._mqtt))

        # Mem0 cross-node search tool (only when mem0 enabled)
        if self._mem0_client:
            self._tools.register(CrossNodeRecallTool(self._mem0_client))

        # Conversation history for context
        self._system_message = {
            "role": "system",
            "content": config.agent.system_prompt,
        }

    async def start(self) -> None:
        """Start the agent."""
        logger.info(f"Starting Smollama agent: {self.config.node.name}")

        # Initialize GPIO
        self._gpio.setup()

        # Connect memory store
        self._memory.connect()
        logger.info("Memory store connected")

        # Connect to MQTT
        connected = await self._mqtt.connect()
        if not connected:
            logger.error("Failed to connect to MQTT broker")
            raise RuntimeError("MQTT connection failed")

        # Start observation loop if enabled
        if self._observation_loop:
            await self._observation_loop.start()

        # Start Mem0 bridge if enabled (Llama node only)
        if self._mem0_bridge:
            await self._mem0_bridge.start()

        # Start discovery manager if enabled
        # Note: In agent-only mode, discovery will browse but won't announce
        # (no HTTP server port to announce)
        if self._discovery_manager:
            await self._discovery_manager.start()
            logger.info("Discovery manager started")

            # If we have sync enabled but no URL, try discovery
            if self._sync_client and not self._sync_client.remote_url:
                logger.info("Waiting for Llama node discovery...")
                await self._discovery_manager.wait_for_discovery(
                    timeout=self.config.discovery.discovery_timeout_seconds
                )

                # Look for Llama node
                nodes = await self._discovery_manager._browser.get_discovered_nodes()
                llama_nodes = [n for n in nodes if n["node_type"] == "llama"]

                if llama_nodes:
                    llama_url = llama_nodes[0]["url"]
                    logger.info(f"Discovered Llama node at {llama_url}")
                    self._sync_client.set_remote_url(llama_url)
                else:
                    logger.warning("No Llama node discovered, sync disabled")

        # Start sync loop if enabled and URL configured
        if self._sync_client and self._sync_client.remote_url:
            self._sync_loop_task = asyncio.create_task(
                self._sync_client.sync_loop(
                    interval_seconds=self.config.sync.sync_interval_minutes * 60,
                    stop_event=self._stop_event,
                )
            )
            logger.info("Sync loop started")

        self._running = True
        logger.info("Agent started successfully")

        # Run main loop
        await self._run_loop()

    async def stop(self) -> None:
        """Stop the agent."""
        logger.info("Stopping agent...")
        self._running = False

        # Stop observation loop
        if self._observation_loop:
            await self._observation_loop.stop()

        # Stop Mem0 bridge
        if self._mem0_bridge:
            await self._mem0_bridge.stop()

        # Close Mem0 client
        if self._mem0_client:
            await self._mem0_client.close()

        # Stop sync loop
        if self._sync_client and hasattr(self, '_sync_loop_task'):
            self._stop_event.set()
            await self._sync_loop_task
            logger.info("Sync loop stopped")

        # Stop discovery manager
        if self._discovery_manager:
            await self._discovery_manager.stop()
            logger.info("Discovery manager stopped")

        # Disconnect from services
        await self._mqtt.disconnect()
        self._gpio.cleanup()
        self._memory.close()

        logger.info("Agent stopped")

    async def _run_loop(self) -> None:
        """Main event loop processing MQTT messages."""
        logger.info("Entering main event loop")

        while self._running:
            try:
                # Wait for next MQTT message
                message = await self._mqtt.get_message(timeout=1.0)
                if message:
                    await self._handle_message(message)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}", exc_info=True)
                await asyncio.sleep(1)

    async def _handle_message(self, message: Message) -> None:
        """Handle an incoming MQTT message.

        Args:
            message: The MQTT message to process.
        """
        logger.info(f"Processing message from {message.topic}: {message.payload[:100]}")

        # Build context for LLM
        context = (
            f"You received an MQTT message on topic '{message.topic}':\n"
            f"{message.payload}\n\n"
            "Respond appropriately. You can use tools to read sensors or "
            "publish messages to other nodes."
        )

        # Run the agentic loop
        response = await self._run_agent_loop(context)

        if response:
            logger.info(f"Agent response: {response}")

    async def _run_agent_loop(
        self,
        user_message: str,
        max_iterations: int | None = None,
    ) -> str | None:
        """Run the agentic tool loop.

        Args:
            user_message: Initial user/trigger message.
            max_iterations: Maximum tool call iterations (uses config default if None).

        Returns:
            Final text response from the LLM.
        """
        # Use config default if not explicitly specified
        if max_iterations is None:
            max_iterations = self.config.agent.max_tool_iterations

        # Enforce minimum of 1 iteration
        max_iterations = max(1, max_iterations)

        messages = [
            self._system_message,
            {"role": "user", "content": user_message},
        ]

        tools = self._tools.to_ollama_format()

        for iteration in range(max_iterations):
            logger.debug(f"Agent loop iteration {iteration + 1}")

            # Call LLM with retry logic
            response = None
            last_error = None

            for attempt in range(self.config.agent.ollama_retry_attempts):
                try:
                    response = await self._ollama.chat(messages, tools)
                    break  # Success, exit retry loop
                except Exception as e:
                    last_error = e
                    if attempt < self.config.agent.ollama_retry_attempts - 1:
                        # Calculate exponential backoff
                        backoff = self.config.agent.ollama_retry_backoff_seconds * (2 ** attempt)
                        logger.warning(
                            f"LLM call failed (attempt {attempt + 1}/{self.config.agent.ollama_retry_attempts}): {e}. "
                            f"Retrying in {backoff:.1f}s..."
                        )
                        await asyncio.sleep(backoff)
                    else:
                        logger.error(
                            f"LLM call failed after {self.config.agent.ollama_retry_attempts} attempts: {e}"
                        )

            # If all retries failed, handle based on fallback mode
            if response is None:
                logger.warning(
                    f"Operating in degraded mode. Fallback mode: {self.config.agent.ollama_fallback_mode}"
                )
                if self.config.agent.ollama_fallback_mode == "skip":
                    return None
                elif self.config.agent.ollama_fallback_mode == "queue":
                    # Queue mode not yet implemented
                    logger.warning("Queue mode not implemented, skipping")
                    return None

            # Check if we have tool calls
            if response.has_tool_calls:
                # Execute tools and collect results
                tool_results = await self._execute_tool_calls(response)

                # Add assistant message with tool calls
                messages.append(format_assistant_tool_calls(response.tool_calls))

                # Add tool results
                for result in tool_results:
                    messages.append(result)

            else:
                # No tool calls, we're done
                return response.content

        logger.warning("Agent loop reached max iterations")
        return None

    async def _execute_tool_calls(
        self,
        response: ChatResponse,
    ) -> list[dict[str, Any]]:
        """Execute tool calls from LLM response.

        Args:
            response: LLM response with tool calls.

        Returns:
            List of tool result messages.
        """
        results = []

        for tool_call in response.tool_calls:
            logger.info(f"Executing tool: {tool_call.name}")
            logger.debug(f"Tool arguments: {tool_call.arguments}")

            try:
                result = await self._tools.execute(
                    tool_call.name,
                    tool_call.arguments,
                )
                result_str = json.dumps(result)
                logger.debug(f"Tool result: {result_str}")
            except Exception as e:
                result_str = json.dumps({"error": str(e)})
                logger.error(f"Tool execution failed: {e}")

            results.append(format_tool_result(tool_call.name, result_str))

        return results

    async def query(self, prompt: str) -> str | None:
        """Send a direct query to the agent.

        Args:
            prompt: Query prompt.

        Returns:
            Agent response.
        """
        return await self._run_agent_loop(prompt)


async def run_agent(config: Config) -> None:
    """Run the agent until interrupted.

    Args:
        config: Configuration for the agent.
    """
    agent = Agent(config)

    try:
        await agent.start()
    except KeyboardInterrupt:
        pass
    finally:
        await agent.stop()
