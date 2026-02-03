"""MQTT-related tools for the agent."""

from typing import Any

from ..mqtt_client import MQTTClient
from .base import Tool, ToolParameter


class PublishTool(Tool):
    """Tool for publishing MQTT messages."""

    def __init__(self, mqtt_client: MQTTClient):
        self._mqtt = mqtt_client

    @property
    def name(self) -> str:
        return "publish"

    @property
    def description(self) -> str:
        return (
            "Publish a message to an MQTT topic. Use this to communicate with "
            "other nodes or external systems."
        )

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="topic",
                type="string",
                description="The MQTT topic to publish to (e.g., 'status', 'alerts', or full path like 'smollama/other-node/command')",
                required=True,
            ),
            ToolParameter(
                name="message",
                type="string",
                description="The message content to publish",
                required=True,
            ),
        ]

    async def execute(self, topic: str, message: str, **kwargs: Any) -> dict[str, Any]:
        """Publish an MQTT message.

        Args:
            topic: Topic to publish to.
            message: Message content.

        Returns:
            Dict with publish result.
        """
        try:
            success = await self._mqtt.publish(topic, message)
            if success:
                return {
                    "success": True,
                    "topic": topic,
                    "message": message,
                }
            else:
                return {
                    "success": False,
                    "error": "Failed to publish message",
                }
        except Exception as e:
            return {"success": False, "error": str(e)}


class GetRecentMessagesTool(Tool):
    """Tool for retrieving recent MQTT messages."""

    def __init__(self, mqtt_client: MQTTClient):
        self._mqtt = mqtt_client

    @property
    def name(self) -> str:
        return "get_recent_messages"

    @property
    def description(self) -> str:
        return (
            "Get recent messages from MQTT topics. Useful for checking what "
            "other nodes have been saying or reviewing recent events."
        )

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="topic",
                type="string",
                description="Optional topic filter. If not provided, returns messages from all topics.",
                required=False,
            ),
            ToolParameter(
                name="count",
                type="integer",
                description="Maximum number of messages to return (default: 10)",
                required=False,
            ),
        ]

    async def execute(
        self,
        topic: str | None = None,
        count: int = 10,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Get recent MQTT messages.

        Args:
            topic: Optional topic filter.
            count: Maximum number of messages.

        Returns:
            Dict with recent messages.
        """
        try:
            # Handle count being passed as string
            if isinstance(count, str):
                count = int(count)

            messages = self._mqtt.get_recent_messages(topic, count)
            return {
                "count": len(messages),
                "messages": [
                    {
                        "topic": msg.topic,
                        "payload": msg.payload,
                        "timestamp": msg.timestamp,
                    }
                    for msg in messages
                ],
            }
        except Exception as e:
            return {"error": str(e)}
