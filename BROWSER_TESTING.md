# Browser Testing Guide (LLM + Chrome MCP)

A guide for LLMs using Claude-in-Chrome MCP tools to manually test the Smollama dashboard.

## Prerequisites

### Start the Dashboard

```bash
# Default (port 8080)
smollama dashboard

# Custom port
smollama dashboard -p 3000
```

The dashboard URL is `http://localhost:8080` (or whichever port you chose).

For meaningful test data, the memory store should have some observations and memories. Run the agent first (`smollama run`) to generate some, or the dashboard will show empty lists.

### MCP Setup

Ensure the `claude-in-chrome` MCP server is connected. Call `tabs_context_mcp` first to get available tabs, then create a new tab with `tabs_create_mcp` for testing.

---

## Pages & Routes

| Page | URL | Key Elements |
|------|-----|-------------|
| Dashboard Home | `/` | Node name header, stats summary (observation/memory counts), navigation links |
| Readings | `/readings` | Live readings list (source ID, value, unit), GPIO mock toggle button |
| Observations | `/observations` | Observation list with timestamps, type, confidence |
| Memories | `/memories` | Memory list with text, confidence, times confirmed |

### API Endpoints (JSON)

| Endpoint | Method | Params | Returns |
|----------|--------|--------|---------|
| `/api/readings` | GET | - | `{timestamp, readings: [{full_id, value, unit, timestamp, metadata}]}` |
| `/api/observations` | GET | `query`, `limit`, `obs_type` | `{query, count, observations: [...]}` |
| `/api/memories` | GET | `query`, `limit` | `{query, count, memories: [...]}` |
| `/api/stats` | GET | - | `{node_name, timestamp, source_types, source_count, ...}` |

### HTMX Partials (HTML fragments)

| Endpoint | Method | Params | Returns |
|----------|--------|--------|---------|
| `/htmx/readings` | GET | - | Readings list HTML fragment |
| `/htmx/observations` | GET | `query` | Observations list HTML fragment |
| `/htmx/stats` | GET | - | Stats summary HTML fragment |
| `/htmx/gpio-toggle` | GET | - | GPIO toggle button HTML fragment |
| `/api/gpio/mode` | POST | `mock` (form data) | Updated GPIO toggle HTML fragment |

---

## Test Scenarios

### 1. Navigate to Dashboard & Verify Home Page

**Goal**: Confirm the dashboard loads and displays the node name.

```
1. Navigate to http://localhost:8080
2. Read the page to verify:
   - Node name is displayed in the header
   - Navigation links exist (Readings, Observations, Memories)
   - Stats section shows observation/memory counts (may be 0)
```

**MCP tool sequence**:
```
mcp__claude-in-chrome__navigate(url="http://localhost:8080", tabId=TAB_ID)
mcp__claude-in-chrome__read_page(tabId=TAB_ID)
```

Check for: a heading containing the node name, nav links, stats numbers.

### 2. Check Navigation

**Goal**: Verify all nav links work and load the correct pages.

```
1. From home page, find and click "Readings" nav link
2. Verify /readings page loads with readings content
3. Click "Observations" nav link
4. Verify /observations page loads
5. Click "Memories" nav link
6. Verify /memories page loads
7. Click home/dashboard link to return
```

**MCP tool sequence**:
```
mcp__claude-in-chrome__find(query="Readings navigation link", tabId=TAB_ID)
mcp__claude-in-chrome__computer(action="left_click", ref="REF_ID", tabId=TAB_ID)
mcp__claude-in-chrome__read_page(tabId=TAB_ID)
# Repeat for each nav link
```

### 3. Readings Page

**Goal**: Verify sensor readings display correctly.

```
1. Navigate to /readings
2. Verify readings are listed with:
   - Source ID (e.g., "system:cpu_temp", "system:memory_percent")
   - Value (numeric)
   - Unit (e.g., "celsius", "percent")
3. Check for GPIO mock toggle button if GPIO is configured
4. If GPIO toggle exists, click it and verify state changes
```

**MCP tool sequence**:
```
mcp__claude-in-chrome__navigate(url="http://localhost:8080/readings", tabId=TAB_ID)
mcp__claude-in-chrome__read_page(tabId=TAB_ID)
mcp__claude-in-chrome__find(query="GPIO toggle", tabId=TAB_ID)
```

### 4. Observations Page

**Goal**: Verify observation list renders.

```
1. Navigate to /observations
2. Verify observations display with text content and timestamps
3. If a search input exists, type a query and verify filtered results
```

**MCP tool sequence**:
```
mcp__claude-in-chrome__navigate(url="http://localhost:8080/observations", tabId=TAB_ID)
mcp__claude-in-chrome__read_page(tabId=TAB_ID)
# If search input exists:
mcp__claude-in-chrome__find(query="search input", tabId=TAB_ID)
mcp__claude-in-chrome__form_input(ref="REF_ID", value="temperature", tabId=TAB_ID)
```

### 5. Memories Page

**Goal**: Verify memory list renders.

```
1. Navigate to /memories
2. Verify memories display with text content and confidence scores
3. If a search input exists, test it
```

**MCP tool sequence**:
```
mcp__claude-in-chrome__navigate(url="http://localhost:8080/memories", tabId=TAB_ID)
mcp__claude-in-chrome__read_page(tabId=TAB_ID)
```

### 6. API Endpoints (JSON)

**Goal**: Verify all REST endpoints return valid JSON.

Use `javascript_tool` to fetch each endpoint and check the response structure.

```
mcp__claude-in-chrome__javascript_tool(
    action="javascript_exec",
    text="await fetch('/api/readings').then(r => r.json())",
    tabId=TAB_ID
)
```

Check each endpoint:

```javascript
// Readings
await fetch('/api/readings').then(r => r.json())
// Expected: { timestamp: "...", readings: [...] }

// Observations
await fetch('/api/observations').then(r => r.json())
// Expected: { query: "", count: N, observations: [...] }

// Observations with search
await fetch('/api/observations?query=temperature&limit=5').then(r => r.json())
// Expected: { query: "temperature", count: N, observations: [...] }

// Memories
await fetch('/api/memories').then(r => r.json())
// Expected: { query: "", count: N, memories: [...] }

// Stats
await fetch('/api/stats').then(r => r.json())
// Expected: { node_name: "...", timestamp: "...", source_types: [...], source_count: N }
```

### 7. HTMX Partials

**Goal**: Verify partials return HTML fragments (not full pages).

```javascript
// Readings partial
await fetch('/htmx/readings').then(r => r.text())
// Expected: HTML fragment with reading items, no <html> or <body> tags

// Stats partial
await fetch('/htmx/stats').then(r => r.text())
// Expected: HTML fragment with stats numbers
```

**MCP tool sequence**:
```
mcp__claude-in-chrome__javascript_tool(
    action="javascript_exec",
    text="await fetch('/htmx/readings').then(r => r.text())",
    tabId=TAB_ID
)
```

Verify the response contains HTML elements but does NOT contain `<!DOCTYPE` or `<html>` (it should be a fragment, not a full page).

---

## MCP Tool Patterns Reference

### Reading page content
```
mcp__claude-in-chrome__read_page(tabId=TAB_ID, filter="all")
```
Returns the accessibility tree. Use `filter="interactive"` to see only buttons/links/inputs.

### Finding specific elements
```
mcp__claude-in-chrome__find(query="node name heading", tabId=TAB_ID)
mcp__claude-in-chrome__find(query="navigation links", tabId=TAB_ID)
mcp__claude-in-chrome__find(query="reading values", tabId=TAB_ID)
```

### Clicking elements
```
mcp__claude-in-chrome__computer(action="left_click", ref="REF_ID", tabId=TAB_ID)
```
Use `ref` from `find` or `read_page` results. Alternatively use coordinates from a screenshot.

### Checking API responses via JS
```
mcp__claude-in-chrome__javascript_tool(
    action="javascript_exec",
    text="JSON.stringify(await fetch('/api/stats').then(r => r.json()), null, 2)",
    tabId=TAB_ID
)
```

### Taking screenshots for visual verification
```
mcp__claude-in-chrome__computer(action="screenshot", tabId=TAB_ID)
```

---

## Known Issues & Notes

- **Empty dashboard**: If the agent hasn't run yet, observations and memories pages will show empty lists. The readings page should still show system metrics (CPU, memory, disk).
- **GPIO toggle**: Only functional when `GPIOReader` is configured with pins in `config.yaml`. Without GPIO config, the toggle may not appear or will show an error.
- **HTMX partials**: These return HTML fragments, not full pages. Fetching them in a browser address bar will show raw HTML without styling.
- **Mock mode**: When `gpio.mock: true` in config, GPIO readings return simulated values. The toggle button switches between mock and real GPIO (real only works on Raspberry Pi with `RPi.GPIO` installed).
- **Port conflicts**: If port 8080 is in use, start with `-p 3000` or another free port.
