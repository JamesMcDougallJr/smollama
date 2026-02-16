# Quick Wins

Small, self-contained improvements that can each be done in a single session.

## Items

### `--host` flag for dashboard
- **Status**: ✅ Complete
- **Effort**: Trivial
- `smollama dashboard --host 0.0.0.0` is currently hardcoded in `__main__.py:cmd_dashboard`; add a CLI arg like `--port`
- **Implementation**: Added `--host` argument to dashboard parser, defaults to `0.0.0.0`

### `/api/health` endpoint
- **Status**: ✅ Complete
- **Effort**: Trivial
- Return `{"status": "ok", "node": "...", "uptime": ...}` for monitoring and load balancer checks
- Add to `smollama/dashboard/app.py`
- **Implementation**: Returns health status with component availability and counts

### `--json` flag for `smollama status`
- **Status**: ✅ Complete
- **Effort**: Small
- Output machine-readable JSON for scripting and CI
- Modify `cmd_status` in `smollama/__main__.py` to collect results into a dict and optionally `json.dumps` them
- **Implementation**: Refactored `cmd_status` to build data structure first, supports both JSON and human-readable output

### Configurable log level via CLI
- **Status**: ✅ Complete
- **Effort**: Small
- `smollama --log-level warning run` instead of only `-v` for debug
- Update `setup_logging()` and the argparse config in `__main__.py`
- **Implementation**: Added `--log-level` with choices: warning, info, debug; overrides `-v/--verbose`

### Reading source count in status
- **Status**: ✅ Complete
- **Effort**: Trivial
- Show registered `ReadingProvider` count and source IDs in `smollama status` output
- Requires initializing a `ReadingManager` in `cmd_status`
- **Implementation**: Shows source types, counts, and individual sources with graceful degradation
