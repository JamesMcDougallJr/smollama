"""Cluster deploy: push smollama code + per-node config to remote nodes via SSH/rsync."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from .config import _deep_merge, load_cluster_config


# Files/dirs excluded from the rsync payload
_RSYNC_EXCLUDES = [
    ".git",
    "__pycache__",
    "*.py[cod]",
    "*.egg-info",
    ".venv",
    "venv",
    "dist",
    "build",
    "config.yaml",
    "config.local.yaml",
    "cluster.yaml",
    ".env",
    ".DS_Store",
]


def load_cluster_data(cluster_path: str | Path) -> dict[str, Any]:
    """Load the raw cluster.yaml (base + all nodes — not merged)."""
    path = Path(cluster_path).expanduser()
    with open(path) as f:
        return yaml.safe_load(f) or {}


def list_nodes(cluster_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a summary list of all nodes defined in cluster_data."""
    base = cluster_data.get("base") or {}
    nodes = cluster_data.get("nodes") or {}
    summary = []
    for key, node in nodes.items():
        deploy_meta = (node or {}).get("_deploy") or {}
        agent_data = (node or {}).get("agent") or {}
        base_agent = (base or {}).get("agent") or {}
        mode = agent_data.get("mode") or base_agent.get("mode") or "full"
        summary.append(
            {
                "key": key,
                "host": deploy_meta.get("host", key),
                "user": deploy_meta.get("user", ""),
                "mode": mode,
                "writer": bool(deploy_meta.get("writer")),
            }
        )
    return summary


def _node_config_dict(cluster_data: dict, node_key: str) -> dict:
    """Merge base + node section, stripping deploy-only keys."""
    base = cluster_data.get("base") or {}
    nodes = cluster_data.get("nodes") or {}
    node_data = {
        k: v
        for k, v in (nodes.get(node_key) or {}).items()
        if k not in ("_deploy", "writer")
    }
    return _deep_merge(base, node_data)


def _ssh_check(user: str, host: str) -> bool:
    """Return True if passwordless SSH to user@host succeeds."""
    remote = f"{user}@{host}" if user else host
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", remote, "true"],
        capture_output=True,
    )
    return result.returncode == 0


# Shell script run on writer nodes by `smollama deploy --setup` to install
# onnxruntime-gpu 1.11.0 — the NVIDIA-official cp36 aarch64 wheel for JetPack 4.6.x.
# protobuf must be pinned: 3.20+ drops Python 3.6 support.
# numpy must stay <1.24: ORT 1.11 uses the removed np.bool/np.int aliases.
_WRITER_SETUP_SCRIPT = (
    # protobuf: pin to last Py3.6-compatible release before 3.20 dropped support
    "pip3 install 'protobuf==3.19.6' && "
    # numpy: system numpy (1.13.3 from JetPack) is below ORT's declared >=1.16.6,
    # but works at runtime — install ORT without dependency resolution to skip the
    # version check; wheels for numpy 1.16-1.23 don't exist for cp36 aarch64.
    "wget -q https://nvidia.box.com/shared/static/pmsqsiaw4pg9qrbeckcbymho6c01jj4z.whl"
    " -O /tmp/onnxruntime_gpu-1.11.0-cp36-cp36m-linux_aarch64.whl && "
    "pip3 install --no-deps /tmp/onnxruntime_gpu-1.11.0-cp36-cp36m-linux_aarch64.whl && "
    "python3 -c 'import onnxruntime; print(\"[setup] onnxruntime\", onnxruntime.__version__, \"ok\")'"
)


def setup_node(
    node_key: str,
    cluster_data: dict[str, Any],
    dry_run: bool = False,
) -> int:
    """Install onnxruntime-gpu on a remote writer node. Returns 0 on success."""
    nodes = cluster_data.get("nodes") or {}
    if node_key not in nodes:
        print(f"ERROR: node {node_key!r} not in cluster config.", file=sys.stderr)
        return 1

    deploy_meta = (nodes[node_key] or {}).get("_deploy") or {}
    host = deploy_meta.get("host", node_key)
    user = deploy_meta.get("user", "")
    is_writer = bool(deploy_meta.get("writer"))
    remote = f"{user}@{host}" if user else host

    if not is_writer:
        print(f"  {node_key}: not a writer node — skipping onnxruntime setup")
        return 0

    print(f"\n── Setting up {node_key} → {remote} ──")
    return _run(["ssh", remote, _WRITER_SETUP_SCRIPT], dry_run, "install onnxruntime-gpu 1.11.0")


def bootstrap_node(user: str, host: str) -> int:
    """Copy the local SSH public key to user@host (prompts for password once)."""
    remote = f"{user}@{host}" if user else host
    if _ssh_check(user, host):
        print(f"  SSH key already authorised on {host}")
        return 0
    print(f"  Copying SSH key to {remote} (you will be prompted for the remote password)…")
    result = subprocess.run(["ssh-copy-id", remote])
    if result.returncode != 0:
        print(
            f"  ERROR: ssh-copy-id failed. Ensure password auth is enabled on {host} "
            "or add the Pi's public key manually.",
            file=sys.stderr,
        )
    return result.returncode


def _run(cmd: list[str], dry_run: bool, label: str) -> int:
    if dry_run:
        print(f"  [dry-run] {' '.join(cmd)}")
        return 0
    print(f"  {label}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"  ERROR: command failed (exit {result.returncode}): {' '.join(cmd)}", file=sys.stderr)
    return result.returncode


def _ssh_write(user: str, host: str, remote_path: str, content: str, dry_run: bool, label: str) -> int:
    """Write content to a file on the remote host via SSH heredoc."""
    if dry_run:
        print(f"  [dry-run] ssh {user}@{host} 'cat > {remote_path}' <<CONTENT")
        for line in content.splitlines()[:5]:
            print(f"    {line}")
        if content.count("\n") > 5:
            print(f"    ... ({content.count(chr(10))} lines total)")
        return 0

    print(f"  {label}")
    mkdir_cmd = ["ssh", f"{user}@{host}", "mkdir -p ~/.smollama"]
    r = subprocess.run(mkdir_cmd)
    if r.returncode != 0:
        return r.returncode

    write_proc = subprocess.run(
        ["ssh", f"{user}@{host}", f"cat > {remote_path}"],
        input=content.encode(),
    )
    return write_proc.returncode


def deploy_node(
    node_key: str,
    cluster_data: dict[str, Any],
    repo_root: Path,
    dry_run: bool = False,
    restart: bool = False,
    bootstrap: bool = False,
) -> int:
    """Deploy smollama to one node. Returns 0 on success, non-zero on failure."""
    nodes = cluster_data.get("nodes") or {}
    if node_key not in nodes:
        print(f"ERROR: node {node_key!r} not in cluster config.", file=sys.stderr)
        return 1

    deploy_meta = (nodes[node_key] or {}).get("_deploy") or {}
    host = deploy_meta.get("host", node_key)
    user = deploy_meta.get("user", "")
    has_writer = bool(deploy_meta.get("writer"))
    remote = f"{user}@{host}" if user else host
    remote_dir = "~/smollama"

    print(f"\n── Deploying {node_key} → {remote}:{remote_dir} ──")

    # 0. Optional SSH key bootstrap (first-time setup)
    if bootstrap:
        if dry_run:
            print(f"  [dry-run] ssh-copy-id {remote}")
        else:
            rc = bootstrap_node(user, host)
            if rc != 0:
                return rc

    # 1. rsync source
    excludes = []
    for ex in _RSYNC_EXCLUDES:
        excludes += ["--exclude", ex]

    rsync_cmd = [
        "rsync", "-az", "--delete",
        *excludes,
        str(repo_root) + "/",
        f"{remote}:{remote_dir}/",
    ]
    rc = _run(rsync_cmd, dry_run, f"rsync → {remote}:{remote_dir}/")
    if rc != 0:
        return rc

    # 2. Write merged node config
    node_config = _node_config_dict(cluster_data, node_key)
    config_yaml = yaml.dump(node_config, default_flow_style=False, allow_unicode=True)
    rc = _ssh_write(
        user, host,
        "~/.smollama/config.yaml",
        config_yaml,
        dry_run,
        f"write ~/.smollama/config.yaml on {host}",
    )
    if rc != 0:
        return rc

    # 3. Write writer config if flagged
    if has_writer:
        writer_cfg = (nodes[node_key] or {}).get("writer") or {}
        writer_json = json.dumps(writer_cfg, indent=2)
        rc = _ssh_write(
            user, host,
            "~/.smollama/writer_config.json",
            writer_json,
            dry_run,
            f"write ~/.smollama/writer_config.json on {host}",
        )
        if rc != 0:
            return rc

    # 4. Optional service restart
    if restart:
        rc = _run(
            ["ssh", remote, "sudo systemctl restart smollama || true"],
            dry_run,
            f"restart smollama on {host}",
        )

    print(f"  ✓ {node_key} done")
    return 0
