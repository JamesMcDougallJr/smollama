"""Memory system for Smollama nodes.

Provides local storage for:
- Reading logs (ground truth sensor/system data)
- LLM-generated observations with semantic search
- Persistent memories/facts across restarts
"""

from .embeddings import EmbeddingProvider, MockEmbeddings, OllamaEmbeddings
from .local_store import LocalStore
from .observation_loop import ObservationLoop

__all__ = [
    "EmbeddingProvider",
    "MockEmbeddings",
    "OllamaEmbeddings",
    "LocalStore",
    "ObservationLoop",
]
