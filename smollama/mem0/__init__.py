"""Mem0 integration for cross-node semantic memory.

This module provides:
- Mem0Client: Wrapper for mem0 API interactions
- Mem0Bridge: Indexes CRDT entries to mem0 on the Llama node
- CrossNodeRecallTool: Agent tool for cross-node memory search
"""

from .client import Mem0Client
from .bridge import Mem0Bridge
from .tools import CrossNodeRecallTool

__all__ = [
    "Mem0Client",
    "Mem0Bridge",
    "CrossNodeRecallTool",
]
