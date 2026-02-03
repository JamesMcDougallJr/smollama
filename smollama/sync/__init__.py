"""Sync infrastructure for distributed Smollama nodes.

Provides CRDT-based append-only event log for offline-first synchronization
between nodes that may be disconnected for extended periods.
"""

from .crdt_log import CRDTLog, LogEntry
from .sync_client import SyncClient

__all__ = ["CRDTLog", "LogEntry", "SyncClient"]
