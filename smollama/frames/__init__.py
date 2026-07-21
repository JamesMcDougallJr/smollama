"""Frame search: CLIP-embedded camera keyframes with local vector search.

Edge nodes (e.g. the Jetson Nano writer) embed sampled camera frames with a
CLIP image encoder and drop {json, jpg} pairs into a spool directory. The edge
agent relays them over MQTT to the master, which stores the embedding in a
sqlite-vec index and the thumbnail on disk. Search encodes a text query with
the matching CLIP text encoder and runs KNN over the index.

See docs/frame-search.md for the full architecture and setup.
"""

from .activity_matcher import ActivityMatcher
from .frame_store import FrameStore
from .spool import FrameSpool
from .text_encoder import ClipTextEncoder

__all__ = ["FrameStore", "FrameSpool", "ClipTextEncoder", "ActivityMatcher"]
