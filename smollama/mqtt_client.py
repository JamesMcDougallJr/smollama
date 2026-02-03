"""MQTT client for pub/sub messaging."""

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

import paho.mqtt.client as mqtt

from .config import MQTTConfig

logger = logging.getLogger(__name__)


@dataclass
class Message:
    """An MQTT message."""

    topic: str
    payload: str
    timestamp: float = field(default_factory=time.time)


MessageCallback = Callable[[Message], None]


class MQTTClient:
    """Async MQTT client for pub/sub messaging."""

    def __init__(
        self,
        config: MQTTConfig,
        on_message: MessageCallback | None = None,
        message_history_size: int = 100,
    ):
        self.config = config
        self._on_message = on_message
        self._message_history_size = message_history_size

        # Message history per topic
        self._message_history: dict[str, deque[Message]] = {}

        # Paho MQTT client
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self._client.on_connect = self._handle_connect
        self._client.on_message = self._handle_message
        self._client.on_disconnect = self._handle_disconnect

        # Connection state
        self._connected = False
        self._loop: asyncio.AbstractEventLoop | None = None

        # Queue for async message handling
        self._message_queue: asyncio.Queue[Message] | None = None

    def _handle_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any = None,
    ) -> None:
        """Handle connection to broker."""
        if reason_code == 0:
            self._connected = True
            logger.info(f"Connected to MQTT broker at {self.config.broker}:{self.config.port}")

            # Subscribe to configured topics
            for topic in self.config.topics.subscribe:
                client.subscribe(topic)
                logger.info(f"Subscribed to topic: {topic}")
        else:
            logger.error(f"Failed to connect to MQTT broker: {reason_code}")

    def _handle_message(
        self,
        client: mqtt.Client,
        userdata: Any,
        msg: mqtt.MQTTMessage,
    ) -> None:
        """Handle incoming message."""
        try:
            payload = msg.payload.decode("utf-8")
        except UnicodeDecodeError:
            payload = str(msg.payload)

        message = Message(topic=msg.topic, payload=payload)

        # Store in history
        if msg.topic not in self._message_history:
            self._message_history[msg.topic] = deque(maxlen=self._message_history_size)
        self._message_history[msg.topic].append(message)

        logger.debug(f"Received message on {msg.topic}: {payload[:100]}")

        # Put in async queue if available
        if self._message_queue and self._loop:
            self._loop.call_soon_threadsafe(
                self._message_queue.put_nowait, message
            )

    def _handle_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        disconnect_flags: Any,
        reason_code: Any,
        properties: Any = None,
    ) -> None:
        """Handle disconnection from broker."""
        self._connected = False
        logger.warning(f"Disconnected from MQTT broker: {reason_code}")

    async def connect(self) -> bool:
        """Connect to the MQTT broker.

        Returns:
            True if connection successful.
        """
        self._loop = asyncio.get_event_loop()
        self._message_queue = asyncio.Queue()

        # Set credentials if configured
        if self.config.username and self.config.password:
            self._client.username_pw_set(self.config.username, self.config.password)

        try:
            self._client.connect(self.config.broker, self.config.port, keepalive=60)
            self._client.loop_start()

            # Wait for connection
            for _ in range(50):  # 5 second timeout
                if self._connected:
                    return True
                await asyncio.sleep(0.1)

            logger.error("Timeout waiting for MQTT connection")
            return False

        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}")
            return False

    async def disconnect(self) -> None:
        """Disconnect from the MQTT broker."""
        self._client.loop_stop()
        self._client.disconnect()
        self._connected = False

    async def publish(self, topic: str, payload: str) -> bool:
        """Publish a message to a topic.

        Args:
            topic: Topic to publish to.
            payload: Message payload.

        Returns:
            True if publish successful.
        """
        if not self._connected:
            logger.error("Cannot publish: not connected to broker")
            return False

        # Use configured prefix if topic doesn't start with smollama/
        if not topic.startswith("smollama/"):
            full_topic = f"{self.config.topics.publish_prefix}/{topic}"
        else:
            full_topic = topic

        result = self._client.publish(full_topic, payload)
        return result.rc == mqtt.MQTT_ERR_SUCCESS

    async def get_message(self, timeout: float | None = None) -> Message | None:
        """Get the next message from the queue.

        Args:
            timeout: Optional timeout in seconds.

        Returns:
            Message or None if timeout.
        """
        if not self._message_queue:
            return None

        try:
            if timeout is None:
                return await self._message_queue.get()
            else:
                return await asyncio.wait_for(
                    self._message_queue.get(), timeout=timeout
                )
        except asyncio.TimeoutError:
            return None

    def get_recent_messages(
        self, topic: str | None = None, count: int = 10
    ) -> list[Message]:
        """Get recent messages from history.

        Args:
            topic: Optional topic filter. If None, returns from all topics.
            count: Maximum number of messages to return.

        Returns:
            List of recent messages, newest first.
        """
        messages: list[Message] = []

        if topic:
            # Get from specific topic
            if topic in self._message_history:
                messages = list(self._message_history[topic])
        else:
            # Get from all topics
            for topic_messages in self._message_history.values():
                messages.extend(topic_messages)

        # Sort by timestamp descending and limit
        messages.sort(key=lambda m: m.timestamp, reverse=True)
        return messages[:count]

    @property
    def is_connected(self) -> bool:
        """Check if connected to broker."""
        return self._connected

    async def check_connection(self) -> bool:
        """Check if broker is reachable."""
        if self._connected:
            return True

        # Try a quick connection test
        try:
            test_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            test_client.connect(self.config.broker, self.config.port, keepalive=5)
            test_client.disconnect()
            return True
        except Exception:
            return False
