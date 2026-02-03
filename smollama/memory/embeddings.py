"""Embedding providers for semantic search in the memory system."""

import hashlib
import logging
import struct
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class EmbeddingProvider(ABC):
    """Abstract base for embedding generation."""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Return the embedding dimension."""
        pass

    @abstractmethod
    def embed(self, text: str) -> bytes:
        """Generate embedding for a single text.

        Args:
            text: Text to embed.

        Returns:
            Embedding as bytes (packed floats for sqlite-vec).
        """
        pass

    def embed_batch(self, texts: list[str]) -> list[bytes]:
        """Generate embeddings for multiple texts.

        Default implementation calls embed() for each text.
        Subclasses may override for batched efficiency.

        Args:
            texts: List of texts to embed.

        Returns:
            List of embeddings as bytes.
        """
        return [self.embed(text) for text in texts]

    @staticmethod
    def floats_to_bytes(floats: list[float]) -> bytes:
        """Convert list of floats to packed bytes for sqlite-vec.

        Args:
            floats: List of float values.

        Returns:
            Packed bytes in little-endian float32 format.
        """
        return struct.pack(f"<{len(floats)}f", *floats)

    @staticmethod
    def bytes_to_floats(data: bytes) -> list[float]:
        """Convert packed bytes back to floats.

        Args:
            data: Packed bytes from sqlite-vec.

        Returns:
            List of float values.
        """
        count = len(data) // 4
        return list(struct.unpack(f"<{count}f", data))


class MockEmbeddings(EmbeddingProvider):
    """Deterministic hash-based embeddings for testing.

    Produces consistent embeddings based on text hash,
    allowing tests to verify semantic search without a real model.
    """

    def __init__(self, dimension: int = 384):
        """Initialize mock embeddings.

        Args:
            dimension: Embedding dimension (default: 384 to match all-minilm).
        """
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        """Return the embedding dimension."""
        return self._dimension

    def embed(self, text: str) -> bytes:
        """Generate deterministic embedding from text hash.

        Args:
            text: Text to embed.

        Returns:
            Embedding as bytes.
        """
        # Use SHA-256 to generate deterministic values
        hash_bytes = hashlib.sha256(text.encode()).digest()

        # Expand hash to fill dimension with pseudo-random but deterministic values
        floats = []
        for i in range(self._dimension):
            # Mix hash bytes with index for variety
            seed = hash_bytes[i % 32] ^ (i & 0xFF)
            # Normalize to [-1, 1] range
            value = (seed / 127.5) - 1.0
            floats.append(value)

        return self.floats_to_bytes(floats)


class OllamaEmbeddings(EmbeddingProvider):
    """Embedding provider using Ollama's embedding models.

    Uses all-minilm:l6-v2 by default (45MB, 384 dimensions).
    """

    def __init__(
        self,
        model: str = "all-minilm:l6-v2",
        host: str = "http://localhost:11434",
    ):
        """Initialize Ollama embeddings.

        Args:
            model: Ollama embedding model name.
            host: Ollama server URL.
        """
        self._model = model
        self._host = host
        self._client = None
        self._dimension = 384  # Default for all-minilm

    @property
    def dimension(self) -> int:
        """Return the embedding dimension."""
        return self._dimension

    def _get_client(self):
        """Lazily initialize Ollama client."""
        if self._client is None:
            try:
                import ollama

                self._client = ollama.Client(host=self._host)
            except ImportError:
                raise RuntimeError(
                    "ollama package not installed. Install with: pip install ollama"
                )
        return self._client

    def embed(self, text: str) -> bytes:
        """Generate embedding using Ollama.

        Args:
            text: Text to embed.

        Returns:
            Embedding as bytes.
        """
        client = self._get_client()

        try:
            response = client.embed(model=self._model, input=text)
            embeddings = response.get("embeddings", [[]])[0]

            if not embeddings:
                logger.warning(f"Empty embedding returned for text: {text[:50]}...")
                # Return zero vector as fallback
                return self.floats_to_bytes([0.0] * self._dimension)

            # Update dimension based on actual response
            if len(embeddings) != self._dimension:
                self._dimension = len(embeddings)

            return self.floats_to_bytes(embeddings)

        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
            # Return zero vector as fallback
            return self.floats_to_bytes([0.0] * self._dimension)

    def embed_batch(self, texts: list[str]) -> list[bytes]:
        """Generate embeddings for multiple texts using batched API.

        Args:
            texts: List of texts to embed.

        Returns:
            List of embeddings as bytes.
        """
        if not texts:
            return []

        client = self._get_client()

        try:
            response = client.embed(model=self._model, input=texts)
            all_embeddings = response.get("embeddings", [])

            results = []
            for embeddings in all_embeddings:
                if not embeddings:
                    results.append(self.floats_to_bytes([0.0] * self._dimension))
                else:
                    if len(embeddings) != self._dimension:
                        self._dimension = len(embeddings)
                    results.append(self.floats_to_bytes(embeddings))

            # Ensure we have one embedding per input
            while len(results) < len(texts):
                results.append(self.floats_to_bytes([0.0] * self._dimension))

            return results

        except Exception as e:
            logger.error(f"Batch embedding generation failed: {e}")
            # Return zero vectors as fallback
            return [self.floats_to_bytes([0.0] * self._dimension) for _ in texts]
