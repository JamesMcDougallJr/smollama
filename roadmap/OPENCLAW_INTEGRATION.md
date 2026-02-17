# OpenClaw Integration Roadmap

## Overview

This document analyzes integration opportunities between **Smollama** (distributed LLM coordination for edge devices) and **OpenClaw** (agentic AI gateway with WebSocket-based skill system). Both projects are complementary:

- **Smollama** excels at: Edge sensing, offline-first memory, distributed sync, GPIO control
- **OpenClaw** excels at: Centralized orchestration, WebSocket multiplexing, skill composition, human messaging

Integration unlocks powerful hybrid architectures where edge intelligence (smollama) combines with cloud orchestration (OpenClaw).

## Architecture Context

### Smollama Architecture
```
Smollama Node
├── Readings Manager (GPIO, System, MQTT)
├── Memory Store (SQLite + vectors)
├── Sync Client (CRDT log, Lamport timestamps)
├── Agent (Tool loop)
├── Dashboard (FastAPI + HTMX)
└── Tools (GPIO, MQTT, Memory, Readings)
```

**Key files:**
- `smollama/agent.py` - Main agent loop
- `smollama/tools/base.py` - Tool interface
- `smollama/mqtt_client.py` - MQTT pub/sub
- `smollama/mem0/bridge.py` - Memory bridge pattern
- `smollama/dashboard/app.py` - FastAPI REST API

### OpenClaw Architecture
```
OpenClaw Gateway
├── WebSocket Server (bidirectional messaging)
├── Skill Registry (dynamic skill loading)
├── Session Manager (multi-session context)
├── Tool Bridge (cross-skill tool sharing)
└── Messaging Layer (WhatsApp, Telegram, Email)
```

**Key integration points:**
- WebSocket client protocol
- Skill interface (Python/TypeScript)
- Tool bridge system
- REST API (for simple queries)

## Integration Approaches

### 1. Gateway WebSocket Client ⭐ **Foundation Layer**

**Effort:** Medium | **Value:** High | **Priority:** 1st

**Description:**
Add WebSocket client to smollama that connects to OpenClaw gateway. Enables bidirectional messaging, remote tool invocation, and coordinated multi-node operations.

**Implementation:**
- New file: `smollama/openclaw/gateway_client.py`
- WebSocket client using `websockets` library
- Heartbeat, reconnection, and offline queue
- Message types: `tool_call`, `tool_response`, `event`, `registration`

**Benefits:**
- Smollama nodes can be remotely orchestrated
- OpenClaw can invoke smollama tools (GPIO, sensors, memory)
- Bidirectional event streaming
- Foundation for all other integrations

**Architecture:**
```python
# smollama/openclaw/gateway_client.py
class OpenClawGatewayClient:
    """WebSocket client for OpenClaw gateway."""

    async def connect(self, gateway_url: str) -> None:
        """Establish WebSocket connection."""

    async def register_node(self, capabilities: dict) -> None:
        """Register node with gateway."""

    async def send_event(self, event_type: str, payload: dict) -> None:
        """Send event to gateway."""

    async def handle_tool_call(self, tool_name: str, args: dict) -> Any:
        """Execute tool call from gateway."""
```

**Configuration:**
```yaml
openclaw:
  enabled: true
  gateway_url: "ws://openclaw.local:3000/ws"
  node_capabilities:
    - gpio_control
    - sensor_reading
    - semantic_memory
  reconnect_interval: 5
```

**Next steps:**
- Implement WebSocket client with reconnection
- Add tool call routing (gateway → smollama tools)
- Add event publishing (sensor readings → gateway)

---

### 2. Smollama as OpenClaw Skill ⭐ **Quick Win**

**Effort:** Small | **Value:** High | **Priority:** 2nd

**Description:**
Package smollama's REST API as an OpenClaw skill. Zero changes to smollama required - just wraps the existing FastAPI endpoints.

**Implementation:**
- New skill: `openclaw/skills/smollama/`
- Uses smollama's existing REST API (`/api/readings`, `/api/memory/recall`, etc.)
- Exposes as OpenClaw tools: `read_sensor`, `recall_memory`, `list_gpio`

**Benefits:**
- Instant integration with zero smollama changes
- OpenClaw sessions can query smollama nodes
- Works with existing dashboard API
- Can be done in 1-2 hours

**Example OpenClaw skill:**
```typescript
// openclaw/skills/smollama/index.ts
export class SmollamaSkill {
  tools = [
    {
      name: "read_sensor",
      description: "Read sensor from smollama node",
      parameters: {
        node_url: { type: "string" },
        source_id: { type: "string" }
      },
      execute: async ({ node_url, source_id }) => {
        const response = await fetch(`${node_url}/api/readings/${source_id}`);
        return await response.json();
      }
    },
    {
      name: "recall_memory",
      description: "Semantic memory search on smollama node",
      parameters: {
        node_url: { type: "string" },
        query: { type: "string" },
        limit: { type: "number", default: 5 }
      },
      execute: async ({ node_url, query, limit }) => {
        const response = await fetch(`${node_url}/api/memory/recall`, {
          method: "POST",
          body: JSON.stringify({ query, limit })
        });
        return await response.json();
      }
    }
  ];
}
```

**Dashboard endpoints to expose:**
- `GET /api/readings` - List all sensors
- `GET /api/readings/{source_id}` - Read specific sensor
- `POST /api/memory/recall` - Semantic search
- `GET /api/memory/observations` - List observations
- `GET /api/gpio` - List GPIO states

**Next steps:**
- Create OpenClaw skill wrapper (TypeScript)
- Document REST API endpoints in smollama
- Add authentication if needed

---

### 3. Sensor Data Streaming (Pull Model)

**Effort:** Trivial | **Value:** Medium | **Priority:** 3rd

**Description:**
OpenClaw periodically polls smollama REST API for sensor readings. Zero changes to smollama required.

**Implementation:**
- OpenClaw cron job or background task
- Polls `/api/readings` every N seconds
- Stores in OpenClaw session context
- Makes sensor data available to all skills

**Benefits:**
- Real-time sensor awareness in OpenClaw sessions
- Works with existing API
- No smollama modifications needed

**OpenClaw background task:**
```typescript
// openclaw/tasks/sensor-poller.ts
setInterval(async () => {
  const nodes = ["http://pi-living:8080", "http://pi-kitchen:8080"];

  for (const nodeUrl of nodes) {
    const readings = await fetch(`${nodeUrl}/api/readings`).then(r => r.json());

    // Store in session context
    sessionManager.updateContext("sensors", {
      [nodeUrl]: readings
    });
  }
}, 30000); // Poll every 30s
```

**Push alternative (requires WebSocket client from #1):**
- Smollama pushes sensor events via WebSocket
- More efficient than polling
- Requires implementing #1 first

---

### 4. OpenClaw as Messaging Layer

**Effort:** Medium | **Value:** High | **Priority:** 4th

**Description:**
Use OpenClaw's messaging integrations (WhatsApp, Telegram, Email) for smollama alerts and notifications.

**Implementation:**
- New smollama tool: `notify_human(message, urgency, channel)`
- Routes through OpenClaw messaging skills
- Requires WebSocket client (#1) or REST callback

**Benefits:**
- Human-in-the-loop for critical events
- Multi-channel notifications (SMS, WhatsApp, Email)
- Leverages OpenClaw's messaging infrastructure

**Smollama tool:**
```python
# smollama/tools/openclaw_tools.py
class NotifyHumanTool(Tool):
    """Send notification via OpenClaw messaging layer."""

    async def execute(self, message: str, urgency: str = "normal", channel: str = "auto"):
        """Send notification to human operator.

        Args:
            message: Notification text
            urgency: "low" | "normal" | "high" | "critical"
            channel: "auto" | "whatsapp" | "telegram" | "email"
        """
        await self.gateway_client.send_event("human_notification", {
            "node_id": self.node_name,
            "message": message,
            "urgency": urgency,
            "channel": channel
        })
```

**Use cases:**
- `notify_human("Motion detected in garage at 3am", urgency="high")`
- `notify_human("CPU temperature critical: 85°C", urgency="critical")`
- `notify_human("All systems nominal", urgency="low")`

---

### 5. Bidirectional Tool Bridging

**Effort:** Large | **Value:** High | **Priority:** 5th

**Description:**
Smollama tools callable from OpenClaw AND OpenClaw tools callable from smollama. Full bidirectional integration.

**Implementation:**
- Extends WebSocket client (#1)
- Tool registry sync between systems
- RPC-style tool invocation
- Type mapping (smollama Python ↔ OpenClaw TypeScript)

**Benefits:**
- OpenClaw skills can control GPIO via smollama
- Smollama agents can use OpenClaw tools (web search, API calls)
- Unified tool ecosystem across edge and cloud

**Architecture:**
```
OpenClaw Session
    ↓ (tool_call: read_gpio)
Gateway WebSocket
    ↓
Smollama Node
    ↓ (execute: GPIOTools.read_gpio)
Result ↑
    ↑
Gateway
    ↑
OpenClaw Session
```

**Tool bridging config:**
```yaml
# smollama config.yaml
openclaw:
  tool_bridge:
    export_tools:
      - read_gpio
      - write_gpio
      - recall_memory
      - read_source
    import_tools:
      - web_search
      - fetch_url
      - run_python
```

**Implementation files:**
- `smollama/openclaw/tool_bridge.py` - Tool proxy and routing
- `smollama/openclaw/tool_schema.py` - Schema conversion
- OpenClaw side: `skills/smollama-bridge/` - Remote tool execution

---

### 6. Shared Memory Bridge (Mem0 Pattern)

**Effort:** Medium | **Value:** Medium | **Priority:** 6th

**Description:**
Follow the Mem0Bridge pattern (`smollama/mem0/bridge.py`) to index smollama observations/memories into OpenClaw's memory system.

**Implementation:**
- New file: `smollama/openclaw/memory_bridge.py`
- Polls CRDT log for new entries
- Sends to OpenClaw memory API
- Enables cross-system semantic search

**Benefits:**
- OpenClaw sessions can search smollama memories
- Unified memory across edge and cloud
- Follows established bridge pattern

**Architecture:**
```python
# smollama/openclaw/memory_bridge.py
class OpenClawMemoryBridge:
    """Bridge that syncs smollama memories to OpenClaw.

    Similar to Mem0Bridge but targets OpenClaw memory API.
    """

    async def _index_observation(self, entry: LogEntry) -> None:
        """Send observation to OpenClaw."""
        await self.openclaw_client.add_memory(
            text=entry.payload["text"],
            node_id=entry.node_id,
            metadata={
                "lamport_ts": entry.lamport_ts,
                "type": "observation",
                "confidence": entry.payload.get("confidence", 0.5)
            }
        )
```

**Reference implementation:**
- See `smollama/mem0/bridge.py` for pattern
- Polls CRDT log every N seconds
- Indexes observations and memories
- Tracks indexed IDs to avoid duplicates

---

### 7. Node Registration & Discovery

**Effort:** Medium | **Value:** Medium | **Priority:** 7th

**Description:**
Smollama nodes auto-register with OpenClaw gateway on startup. Gateway maintains registry of available nodes and capabilities.

**Implementation:**
- Registration message on WebSocket connect
- Heartbeat for liveness detection
- Capability discovery (GPIO pins, sensors, tools)
- Gateway maintains node registry

**Benefits:**
- Dynamic node discovery (no hard-coded URLs)
- Automatic failover detection
- Capability-based routing

**Registration payload:**
```json
{
  "type": "node_registration",
  "node_id": "pi-living-room",
  "capabilities": {
    "tools": ["read_gpio", "write_gpio", "recall_memory"],
    "sensors": ["system:cpu_temp", "gpio:motion", "gpio:light_level"],
    "gpio_pins": [17, 18, 22, 23],
    "features": ["offline_sync", "semantic_memory", "mqtt_bridge"]
  },
  "metadata": {
    "platform": "raspberry-pi-4",
    "python_version": "3.11",
    "smollama_version": "0.1.0"
  }
}
```

**Gateway maintains:**
- Active node list
- Last heartbeat timestamp
- Node capabilities
- Health status

---

### 8. Dashboard Integration

**Effort:** Medium | **Value:** Low | **Priority:** 8th

**Description:**
Embed smollama dashboard views in OpenClaw UI. Display sensor readings, memory, and node status alongside OpenClaw sessions.

**Implementation:**
- Iframe embedding of smollama dashboard
- Or: Rebuild smollama views in OpenClaw UI
- Unified navigation between systems

**Benefits:**
- Single pane of glass
- Monitor all nodes from OpenClaw
- Simplified operations

**Low priority because:**
- Both have functional dashboards independently
- Integration effort doesn't add much value
- Focus on data/tool integration first

---

### 9. Session Coordination

**Effort:** Large | **Value:** Medium | **Priority:** 9th

**Description:**
OpenClaw sessions can "take over" smollama agents. Pause local decision-making, route all decisions through OpenClaw session.

**Implementation:**
- Session handoff protocol
- Dual-mode agent: autonomous vs. supervised
- Context sharing (smollama state → OpenClaw session)

**Benefits:**
- Remote debugging of smollama behavior
- Human oversight of edge decisions
- Hybrid autonomous/supervised operation

**Architecture:**
```
Normal Mode:
  Smollama Agent → Makes decisions locally

Supervised Mode:
  Smollama Agent → Defers to OpenClaw session
                 ↓
            OpenClaw Session
                 ↓
            Human in loop
```

**Use cases:**
- Debugging weird agent behavior
- High-stakes decisions (unlock door, send alert)
- Training new behaviors (learn from human)

**Complex because:**
- Requires careful state synchronization
- Latency considerations (network delay)
- Fallback to local if connection lost

---

## Recommended Implementation Order

### Phase 1: Foundation (Week 1-2)
1. **Gateway WebSocket Client** - Core bidirectional messaging
2. **Smollama as OpenClaw Skill** - Quick win, immediate value

### Phase 2: Data Flow (Week 3-4)
3. **Sensor Data Streaming** - Real-time awareness in OpenClaw
4. **OpenClaw as Messaging Layer** - Human alerts via WhatsApp/etc.

### Phase 3: Deep Integration (Month 2)
5. **Bidirectional Tool Bridging** - Unified tool ecosystem
6. **Shared Memory Bridge** - Cross-system semantic search
7. **Node Registration** - Dynamic discovery

### Phase 4: Polish (Month 3+)
8. **Dashboard Integration** - Unified UI (low priority)
9. **Session Coordination** - Supervised mode (complex, optional)

## Integration Patterns

### Pattern 1: Pull Integration (Lightweight)
- OpenClaw polls smollama REST API
- Zero smollama changes required
- Good for: sensor reading, memory queries
- Examples: #2 (REST Skill), #3 (Sensor Polling)

### Pattern 2: Push Integration (Efficient)
- Smollama pushes events via WebSocket
- Requires WebSocket client (#1)
- Good for: real-time events, alerts
- Examples: #1 (Gateway Client), #4 (Messaging)

### Pattern 3: Bridge Pattern (Async Sync)
- Background process syncs data between systems
- Follows Mem0Bridge architecture
- Good for: memory, logs, observations
- Examples: #6 (Memory Bridge)

### Pattern 4: RPC Pattern (Bidirectional)
- Tools callable across system boundaries
- Request/response over WebSocket
- Good for: remote control, orchestration
- Examples: #5 (Tool Bridge), #9 (Session Coordination)

## Configuration Example

Final integrated config for smollama:

```yaml
# config.yaml
node:
  name: "pi-living-room"

openclaw:
  enabled: true
  gateway_url: "ws://openclaw.local:3000/ws"

  # Which integration features to enable
  features:
    gateway_client: true        # #1: WebSocket client
    rest_api_skill: true        # #2: Expose REST API (no config needed)
    sensor_streaming: true      # #3: Push sensor events
    messaging_layer: true       # #4: Human notifications
    tool_bridge: true          # #5: Bidirectional tools
    memory_bridge: true        # #6: Sync to OpenClaw memory
    node_registration: true    # #7: Auto-register on connect

  # Tool bridging config
  tool_bridge:
    export_tools:              # Smollama tools → OpenClaw
      - read_gpio
      - write_gpio
      - recall_memory
      - observe
      - remember
    import_tools:              # OpenClaw tools → Smollama
      - web_search
      - fetch_url
      - send_whatsapp

  # Memory bridge config
  memory_bridge:
    enabled: true
    sync_interval_seconds: 30
    sync_observations: true
    sync_memories: true

  # Node capabilities (auto-detected, can override)
  capabilities:
    - gpio_control
    - sensor_reading
    - semantic_memory
    - mqtt_bridge
```

## API Compatibility

### Smollama REST API (already exists)
- `GET /api/readings` - List sensors
- `GET /api/readings/{source_id}` - Read sensor
- `POST /api/memory/recall` - Search memories
- `GET /api/gpio` - List GPIO states
- `POST /api/gpio/{pin}` - Control GPIO

### OpenClaw API (to be defined)
- `POST /api/nodes/{node_id}/tool` - Invoke remote tool
- `GET /api/nodes` - List registered nodes
- `POST /api/memory/add` - Add cross-node memory
- `POST /api/messages/send` - Send human notification

## Testing Strategy

### Integration Tests
1. **Gateway Client:**
   - Connect/disconnect/reconnect
   - Heartbeat and liveness
   - Message send/receive

2. **Tool Bridge:**
   - Remote tool invocation (both directions)
   - Error handling and timeouts
   - Type marshalling (Python ↔ TypeScript)

3. **Memory Bridge:**
   - CRDT entry indexing
   - Duplicate detection
   - Backfill on startup

### End-to-End Scenarios
1. **Sensor → Alert:**
   - Smollama detects motion
   - Sends to OpenClaw via WebSocket
   - OpenClaw sends WhatsApp alert

2. **Remote GPIO Control:**
   - OpenClaw session calls `write_gpio(18, 1)`
   - Routed to smollama node
   - LED turns on

3. **Cross-Node Memory Search:**
   - Query OpenClaw: "what was the temperature in the kitchen?"
   - Searches smollama memories from kitchen node
   - Returns relevant observations

## File Structure

New files to create:

```
smollama/
├── openclaw/
│   ├── __init__.py
│   ├── gateway_client.py      # #1: WebSocket client
│   ├── tool_bridge.py         # #5: Tool RPC
│   ├── tool_schema.py         # #5: Schema conversion
│   ├── memory_bridge.py       # #6: Memory sync
│   └── config.py              # OpenClaw-specific config
├── tools/
│   └── openclaw_tools.py      # #4: Notification tools
└── config.py                  # Add OpenClawConfig

openclaw/ (separate repo)
└── skills/
    └── smollama/
        ├── index.ts           # #2: REST API skill
        ├── tool-bridge.ts     # #5: Remote tool execution
        └── types.ts           # Type definitions
```

## Dependencies

### Smollama New Dependencies
```toml
# pyproject.toml
[project.optional-dependencies]
openclaw = [
    "websockets>=12.0",      # WebSocket client
    "msgpack>=1.0.0",        # Efficient serialization
]
```

### OpenClaw New Dependencies
```json
// package.json
{
  "dependencies": {
    "axios": "^1.6.0"         // HTTP client for REST API
  }
}
```

## Success Metrics

### Phase 1 Success (Foundation)
- [ ] Smollama node connects to OpenClaw gateway via WebSocket
- [ ] OpenClaw can read smollama sensors via REST API skill
- [ ] Connection survives network disruption (reconnect works)

### Phase 2 Success (Data Flow)
- [ ] Sensor readings appear in OpenClaw session context within 30s
- [ ] Smollama can trigger WhatsApp alerts via OpenClaw
- [ ] End-to-end latency < 2 seconds for notifications

### Phase 3 Success (Deep Integration)
- [ ] OpenClaw can control smollama GPIO remotely
- [ ] Smollama can use OpenClaw tools (web search)
- [ ] Cross-node memory search works (query kitchen temps from living room)
- [ ] Tool call latency < 500ms (95th percentile)

### Phase 4 Success (Polish)
- [ ] Unified dashboard shows all nodes
- [ ] Session handoff works (autonomous → supervised → autonomous)
- [ ] Zero manual configuration (auto-discovery works)

## Security Considerations

### Authentication
- WebSocket connections should use tokens
- REST API needs API key or JWT
- Consider: Per-node API keys

### Authorization
- Define which tools are remotely callable
- Whitelist approach (explicit export/import)
- Reject unknown tool calls

### Network Security
- Use WSS (WebSocket Secure) in production
- VPN or Tailscale for multi-node deployments
- Rate limiting on gateway

### Data Privacy
- Sensor data may be sensitive (camera, audio)
- Memory contains private observations
- Consider: End-to-end encryption for messages

## Open Questions

1. **Should smollama nodes trust OpenClaw completely?**
   - Pro: Simplifies integration
   - Con: Remote code execution risk
   - Decision: Whitelist tools, audit logs

2. **How to handle version mismatches?**
   - Smollama v0.2 vs OpenClaw expecting v0.1 API
   - Decision: Versioned WebSocket protocol, capability negotiation

3. **What happens if OpenClaw is offline?**
   - Smollama should continue autonomous operation
   - Queue events for later delivery
   - Decision: Offline-first design, degrade gracefully

4. **Should memory bridge be bidirectional?**
   - OpenClaw → Smollama memory sync?
   - Probably not needed (smollama owns edge memories)
   - Decision: One-way for now, revisit if needed

## References

### Smollama Key Files
- `smollama/agent.py` - Agent loop (would call OpenClaw tools)
- `smollama/tools/base.py` - Tool interface (model for bridge)
- `smollama/mem0/bridge.py` - Bridge pattern example
- `smollama/dashboard/app.py` - REST API endpoints
- `smollama/mqtt_client.py` - Pub/sub messaging model

### OpenClaw Key Concepts
- WebSocket-based skill communication
- Skill registry and lifecycle
- Tool bridge pattern for cross-skill tools
- Session-based context management
- Messaging integrations (WhatsApp, Telegram)

## Conclusion

The integration offers high value at relatively low cost:
- **Quick wins:** REST API skill (#2) can be done in hours
- **Foundation:** WebSocket client (#1) unlocks most other features
- **High ROI:** Tool bridging (#5) and messaging (#4) provide major capabilities

**Recommended first milestone:**
Implement #1 (Gateway Client) and #2 (REST Skill) in a 2-day sprint. This provides immediate value (remote sensor reading) and validates the integration architecture before deeper investment.
