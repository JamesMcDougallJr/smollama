"""mDNS/Zeroconf service discovery for Smollama nodes.

Enables automatic discovery of nodes on the local network without manual URL
configuration.
"""

import asyncio
import logging
import socket
from datetime import datetime, timedelta
from typing import Any

from zeroconf import ServiceInfo, ServiceStateChange, Zeroconf
from zeroconf import ServiceBrowser as ZeroconfServiceBrowser
from zeroconf.asyncio import AsyncServiceInfo, AsyncZeroconf

logger = logging.getLogger(__name__)


class ServiceAnnouncer:
    """Announces this node's service via mDNS."""

    def __init__(
        self,
        node_name: str,
        node_type: str,
        port: int,
        service_type: str = "_smollama._tcp",
    ):
        """Initialize the service announcer.

        Args:
            node_name: Unique name for this node.
            node_type: Type of node ("llama" or "alpaca").
            port: Port number where dashboard is listening.
            service_type: mDNS service type (default: "_smollama._tcp").
        """
        self.node_name = node_name
        self.node_type = node_type
        self.port = port
        self.service_type = service_type
        self._zeroconf: AsyncZeroconf | None = None
        self._service_info: ServiceInfo | None = None

    async def start(self) -> None:
        """Start announcing the service."""
        # Get local IP address
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)

        # Create service info
        service_name = f"{self.node_name}.{self.service_type}.local."
        type_name = f"{self.service_type}.local."

        # TXT records with node metadata
        properties = {
            b"node_type": self.node_type.encode("utf-8"),
            b"version": b"0.1.0",
        }

        self._service_info = ServiceInfo(
            type_name,
            service_name,
            addresses=[socket.inet_aton(local_ip)],
            port=self.port,
            properties=properties,
            server=f"{hostname}.local.",
        )

        # Start zeroconf and register service
        self._zeroconf = AsyncZeroconf()
        await self._zeroconf.async_register_service(self._service_info)

        logger.info(
            f"Announcing service: {service_name} at {local_ip}:{self.port} "
            f"(type={self.node_type})"
        )

    async def stop(self) -> None:
        """Stop announcing the service."""
        if self._zeroconf and self._service_info:
            await self._zeroconf.async_unregister_service(self._service_info)
            await self._zeroconf.async_close()
            logger.info("Service announcement stopped")


class ServiceBrowser:
    """Browses for other Smollama nodes via mDNS."""

    def __init__(
        self,
        service_type: str = "_smollama._tcp",
        cache_ttl_seconds: int = 300,
    ):
        """Initialize the service browser.

        Args:
            service_type: mDNS service type to browse for.
            cache_ttl_seconds: How long to cache discovered nodes.
        """
        self.service_type = service_type
        self.cache_ttl_seconds = cache_ttl_seconds
        self._zeroconf: Zeroconf | None = None
        self._browser: ZeroconfServiceBrowser | None = None
        self._discovered: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def _on_service_state_change(
        self,
        zeroconf: Zeroconf,
        service_type: str,
        name: str,
        state_change: ServiceStateChange,
    ) -> None:
        """Handle service state changes (add/remove/update)."""
        if not self._loop:
            return

        if state_change is ServiceStateChange.Added:
            asyncio.run_coroutine_threadsafe(
                self._add_service(zeroconf, service_type, name), self._loop
            )
        elif state_change is ServiceStateChange.Removed:
            asyncio.run_coroutine_threadsafe(self._remove_service(name), self._loop)

    async def _add_service(
        self, zeroconf: Zeroconf, service_type: str, name: str
    ) -> None:
        """Add or update a discovered service."""
        info = AsyncServiceInfo(service_type, name)
        await info.async_request(zeroconf, 3000)  # 3 second timeout

        if not info or not info.addresses:
            return

        # Extract node info from TXT records
        properties = info.properties or {}
        node_type = properties.get(b"node_type", b"unknown").decode("utf-8")

        # Get IP address
        address = socket.inet_ntoa(info.addresses[0])
        url = f"http://{address}:{info.port}"

        # Extract node name from service name
        node_name = name.split(".")[0]

        async with self._lock:
            self._discovered[name] = {
                "node_name": node_name,
                "node_type": node_type,
                "url": url,
                "port": info.port,
                "last_seen": datetime.now(),
            }

        logger.info(f"Discovered node: {node_name} ({node_type}) at {url}")

    async def _remove_service(self, name: str) -> None:
        """Remove a service from the registry."""
        async with self._lock:
            if name in self._discovered:
                node_info = self._discovered.pop(name)
                logger.info(
                    f"Node removed: {node_info['node_name']} ({node_info['node_type']})"
                )

    async def start(self) -> None:
        """Start browsing for services."""
        self._loop = asyncio.get_event_loop()
        self._zeroconf = Zeroconf()
        self._browser = ZeroconfServiceBrowser(
            self._zeroconf,
            f"{self.service_type}.local.",
            handlers=[self._on_service_state_change],
        )
        logger.info(f"Browsing for {self.service_type} services")

    async def stop(self) -> None:
        """Stop browsing for services."""
        if self._browser:
            self._browser.cancel()
        if self._zeroconf:
            self._zeroconf.close()
        logger.info("Service browsing stopped")

    async def get_discovered_nodes(self) -> list[dict[str, Any]]:
        """Get list of discovered nodes.

        Returns:
            List of discovered node info dictionaries.
        """
        async with self._lock:
            # Filter out stale entries
            cutoff = datetime.now() - timedelta(seconds=self.cache_ttl_seconds)
            self._discovered = {
                name: info
                for name, info in self._discovered.items()
                if info["last_seen"] > cutoff
            }

            return list(self._discovered.values())


class DiscoveryManager:
    """High-level coordinator for mDNS discovery.

    Manages both service announcement and browsing.
    """

    def __init__(
        self,
        node_name: str,
        node_type: str,
        port: int,
        service_type: str = "_smollama._tcp",
        announce: bool = True,
        browse: bool = True,
        cache_ttl_seconds: int = 300,
    ):
        """Initialize the discovery manager.

        Args:
            node_name: Unique name for this node.
            node_type: Type of node ("llama" or "alpaca").
            port: Port number where dashboard is listening.
            service_type: mDNS service type.
            announce: Whether to announce this node's service.
            browse: Whether to browse for other nodes.
            cache_ttl_seconds: How long to cache discovered nodes.
        """
        self.node_name = node_name
        self.node_type = node_type
        self.port = port

        self._announcer: ServiceAnnouncer | None = None
        if announce:
            self._announcer = ServiceAnnouncer(
                node_name=node_name,
                node_type=node_type,
                port=port,
                service_type=service_type,
            )

        self._browser: ServiceBrowser | None = None
        if browse:
            self._browser = ServiceBrowser(
                service_type=service_type,
                cache_ttl_seconds=cache_ttl_seconds,
            )

    async def start(self) -> None:
        """Start discovery (announce + browse)."""
        if self._announcer:
            await self._announcer.start()

        if self._browser:
            await self._browser.start()

        logger.info("Discovery manager started")

    async def stop(self) -> None:
        """Stop discovery."""
        if self._announcer:
            await self._announcer.stop()

        if self._browser:
            await self._browser.stop()

        logger.info("Discovery manager stopped")

    def get_discovered_nodes(self) -> list[dict[str, Any]]:
        """Get list of discovered nodes synchronously.

        Returns:
            List of discovered node info dictionaries.
        """
        if not self._browser:
            return []

        # Run the async method in a synchronous context
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(self._browser.get_discovered_nodes())

    async def wait_for_discovery(self, timeout: int = 10) -> None:
        """Wait for at least one node to be discovered.

        Args:
            timeout: Maximum time to wait in seconds.
        """
        if not self._browser:
            return

        start_time = datetime.now()
        while (datetime.now() - start_time).total_seconds() < timeout:
            nodes = await self._browser.get_discovered_nodes()
            if nodes:
                return
            await asyncio.sleep(0.5)

        logger.warning(f"No nodes discovered after {timeout}s timeout")
