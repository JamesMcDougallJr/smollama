# Quick Wins

Small, self-contained improvements that can each be done in a single session.

## Items

### `--host` flag for dashboard
- **Status**: Not started
- **Effort**: Trivial
- `smollama dashboard --host 0.0.0.0` is currently hardcoded in `__main__.py:cmd_dashboard`; add a CLI arg like `--port`

### `/api/health` endpoint
- **Status**: Not started
- **Effort**: Trivial
- Return `{"status": "ok", "node": "...", "uptime": ...}` for monitoring and load balancer checks
- Add to `smollama/dashboard/app.py`

### `--json` flag for `smollama status`
- **Status**: Not started
- **Effort**: Small
- Output machine-readable JSON for scripting and CI
- Modify `cmd_status` in `smollama/__main__.py` to collect results into a dict and optionally `json.dumps` them

### Configurable log level via CLI
- **Status**: Not started
- **Effort**: Small
- `smollama --log-level warning run` instead of only `-v` for debug
- Update `setup_logging()` and the argparse config in `__main__.py`

### Reading source count in status
- **Status**: Not started
- **Effort**: Trivial
- Show registered `ReadingProvider` count and source IDs in `smollama status` output
- Requires initializing a `ReadingManager` in `cmd_status`
