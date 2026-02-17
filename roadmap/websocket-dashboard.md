# WebSocket Real-Time Dashboard

Replace HTMX polling with WebSocket push for real-time dashboard updates.

- **Status**: Not started
- **Effort**: Medium (40-60 hours)
- **Priority**: High
- **Dependencies**: Dashboard ✅, Readings ✅, Observations ✅

## Overview

Currently, the dashboard uses HTMX to poll `/htmx/*` endpoints for partial page updates.
This works but has latency (polling interval) and unnecessary requests. WebSocket enables
instant updates when readings change or observations are created.

## Goals

- Real-time sensor reading updates (no refresh needed)
- Live observation stream as they're created
- Memory updates pushed to connected clients
- Reduced server load (no polling)
- Graceful fallback to HTMX if WebSocket unavailable

## Implementation

### Server Side (FastAPI)
- Add WebSocket endpoint: `/ws`
- Broadcast to all connected clients when:
  - New reading available
  - New observation created
  - Memory updated
- Heartbeat/keepalive mechanism
- Connection registry (track active clients)

### Client Side (JavaScript)
- WebSocket client in dashboard templates
- Handle messages: `reading_update`, `observation_update`, `memory_update`
- Update DOM without full page reload
- Auto-reconnect on disconnect
- Fallback to HTMX polling if WebSocket fails

### Message Protocol
```json
{
  "type": "reading_update",
  "data": {
    "source_id": "gpio:17",
    "value": 1,
    "timestamp": "2026-02-16T10:30:00Z"
  }
}
```

## Files to Modify

- `smollama/dashboard/app.py` - Add WebSocket endpoint
- `smollama/dashboard/templates/base.html` - Add WebSocket client JS
- `smollama/dashboard/templates/readings.html` - Update via WS
- `smollama/dashboard/templates/observations.html` - Update via WS

## Testing

1. Start dashboard: `smollama dashboard`
2. Open browser, inspect network tab
3. Verify WebSocket connection established
4. Trigger reading change (GPIO toggle)
5. Verify dashboard updates without refresh
6. Disconnect WebSocket, verify fallback to HTMX
