# Report: Jetson Nano shows "offline / last seen June 7" on the master dashboard

**Date:** 2026-06-14
**Author:** investigation from the edge node (`james-desktop` = the Jetson Nano)
**For:** a follow-up session run **on the master host** (`10.0.0.223`, node `llama-master`)

---

## TL;DR

The Jetson edge node is **fully fixed and publishing live** to the broker. The remaining
problem is **on the master**: its readings cache is frozen at **2026-06-07T22:07**, so the
dashboard shows the Jetson as offline. The master agent is *running* (in fact **two copies**
are running), so this is **not** a "process is down" issue — it's most likely a **stale /
duplicate smollama install** where the agent actually consuming MQTT doesn't write the cache
the dashboard reads (or the two agents are conflicting). Resolve on the master.

---

## What is already confirmed working (edge side — done, no action needed)

On the Jetson (`james-desktop`):
- Two systemd units created + enabled, both survived a reboot and are **active**:
  - `jetson-infer.service` — Py3.6 vision writer, camera streaming, contract fresh (age ~0s, 16.7 fps)
  - `smollama-edge.service` — `uv run smollama run` (edge mode, no LLM)
- Edge agent connected to broker `10.0.0.223:1883`, `jetson_inference` plugin loaded, publish loop @3s.
- **Verified on the broker** (`mosquitto_sub -h 10.0.0.223 -t 'smollama/jetson-nano/readings'`):
  a payload arrives every ~3s containing all six detection sources
  (`jetson_inference:object_count|person_count|pose_count|top_object|activity|network_fps`)
  plus `system:*`.
- Note: detection **counts are currently 0** (empty scene) — that's not a fault; put something
  in front of the camera to see them rise.

So messages are reaching the master's broker correctly. The Jetson is not the problem.

## What is wrong (master side — to fix in the host session)

- `curl -s http://10.0.0.223:8080/api/readings` returns 14 readings, **all timestamped
  `2026-06-07T22:07`** → the master's `~/.smollama/mqtt_bridge_cache.json` has not been
  written since June 7.
- The dashboard process only *reads* that cache; the **agent** process writes it via
  `MQTTBridgeProvider.ingest_edge_payload` (`smollama/agent.py:336-342`, on any
  `*/readings` topic that isn't its own `publish_prefix` echo).
- The echo-filter is **not** the cause: Jetson publishes to `smollama/jetson-nano/...`,
  and the master's `own_prefix` is `smollama/llama-master`, so messages are **not** dropped
  (`agent.py:326-330`). The known `publish_prefix` gotcha is already handled correctly.
- **Key finding:** the master is running **two** `smollama run` processes from **different
  installs**:
  - `PID 2147  /home/james/smollama/.venv/bin/python3 /usr/local/bin/smollama run` (project venv)
  - `PID 1256597  /home/james/.local/share/uv/tools/smollama/bin/python /home/james/.local/bin/smollama run` (uv-tool global install)
  - `systemctl is-active smollama` = active; `mosquitto` = active.

### Leading hypothesis

One of the two installs is **stale** (predates the MQTT edge-bridge ingest code — that code
is recent, see commits `f1aa1e1`/`b82a0c8`/`4e2f0aa`). The agent that systemd actually runs,
or the one that "wins" the cache file, either doesn't have `ingest_edge_payload` or the two
agents conflict — so new edge readings never update `mqtt_bridge_cache.json`. The cache still
holds the June-7 jetson entries from the last time a correct agent ingested.

---

## Runbook for the host session (run ON the master `10.0.0.223`)

### Step 1 — identify which binary systemd runs, and the versions
```bash
systemctl cat smollama | grep -E "ExecStart|WorkingDirectory"
which -a smollama
/usr/local/bin/smollama --version 2>/dev/null
~/.local/bin/smollama --version 2>/dev/null
uv --version
```
Goal: find out whether systemd launches the project-venv build or the `uv tool` global build,
and whether they differ.

### Step 2 — confirm whether the agent is actually ingesting right now
```bash
# Does the running agent receive + ingest the live jetson messages?
journalctl -u smollama --since "10 min ago" | grep -iE "ingest|readings|jetson|Processing message|error|traceback" | tail -30

# Is the cache stale on disk?
stat -c '%y' ~/.smollama/mqtt_bridge_cache.json
python3 -c "import json,os;d=json.load(open(os.path.expanduser('~/.smollama/mqtt_bridge_cache.json')));import itertools;[print(k, v.get('timestamp')) for k,v in itertools.islice(d.items(),5)]"
```
If the journal shows the agent receiving `smollama/jetson-nano/readings` but the cache mtime
stays at June 7 → the *running* agent is a version without ingest, or a second process owns/holds the file.

### Step 3 — check for the duplicate / verify the bridge code is present
```bash
ps -ef | grep "smollama run" | grep -v grep      # expect to see BOTH PIDs from this report
# Does each install contain the bridge ingest method?
grep -l "ingest_edge_payload" /home/james/smollama/smollama/readings/mqtt_bridge.py
python3 - <<'EOF'
import importlib.util, subprocess
for py in ["/home/james/smollama/.venv/bin/python3", "/home/james/.local/share/uv/tools/smollama/bin/python"]:
    r = subprocess.run([py, "-c", "import smollama.readings.mqtt_bridge as m; print(hasattr(m.MQTTBridgeProvider,'ingest_edge_payload'))"], capture_output=True, text=True)
    print(py, "->", r.stdout.strip() or r.stderr.strip())
EOF
```

### Step 4 — fix: collapse to a single, current install and restart
The intended master setup runs the project from its venv via systemd (matching the Pi setup).
Recommended:
```bash
# 1. Kill the stray/duplicate agent (whichever one is NOT managed by systemd)
#    e.g. the uv-tool global one if systemd runs the venv build:
kill 1256597        # <-- use the actual non-systemd PID from Step 3

# 2. (If the uv-tool install is the stale one) remove it so it can't be launched again:
uv tool uninstall smollama 2>/dev/null || true

# 3. Make sure the systemd-managed install is current, then restart cleanly:
cd /home/james/smollama && git status && git pull --ff-only   # if it tracks the same branch
sudo systemctl restart smollama
sleep 6
systemctl is-active smollama
```
> Decide which to keep based on Steps 1–3: keep the install whose `ingest_edge_payload`
> check returns `True` **and** that matches the current repo, and make systemd point at it.
> The goal state is **exactly one** `smollama run` agent on the master.

### Step 5 — verify end-to-end
```bash
# cache should now advance within seconds
watch -n2 "stat -c '%y' ~/.smollama/mqtt_bridge_cache.json"
# dashboard API should show jetson sources with FRESH (today) timestamps
curl -s http://localhost:8080/api/readings | python3 -c "import sys,json;[print(r.get('source_type'),r.get('source_id'),r.get('timestamp')) for r in json.load(sys.stdin)['readings'] if 'jetson' in str(r).lower()]"
```
Then reload the dashboard — the Jetson should flip to **online** with `jetson-nano:jetson_inference:*`
updating live (counts rise when something is in front of the camera).

### Step 6 — prevent recurrence
- Ensure the master runs **one** install only (remove the `uv tool` global copy if it was the dupe).
- Confirm `systemctl is-enabled smollama` and that its `ExecStart` points at the kept install.
- If the agent died/forked on June 7 due to a crash, capture the cause:
  `journalctl -u smollama --since "2026-06-07" --until "2026-06-08" | grep -iE "error|traceback|exit" | tail -40`.

---

## Quick reference — facts gathered

| Item | Value |
|---|---|
| Edge node | `james-desktop` = Jetson Nano, node `jetson-nano`, publishing OK |
| Master | `10.0.0.223`, node `llama-master`, `publish_prefix: smollama/llama-master` |
| Master dashboard readings | all frozen at `2026-06-07T22:07` |
| Master cache file | `~/.smollama/mqtt_bridge_cache.json` (written by agent, read by dashboard) |
| Ingest code path | `smollama/agent.py:336-342` → `mqtt_bridge.py: ingest_edge_payload` |
| Echo filter | `agent.py:326-330` (does NOT drop jetson topics — not the cause) |
| Master agents running | **2** — venv `PID 2147` + uv-tool `PID 1256597` (the likely problem) |
| mosquitto on master | active |
