"""
cowork.py - Parses Claude Desktop Cowork audit logs.
"""

import json
import os
import re
import sys
from pathlib import Path


def cowork_sessions_dir():
    """Return Claude Desktop's local-agent-mode-sessions directory."""
    if sys.platform == "darwin":
        user_data = Path.home() / "Library" / "Application Support" / "Claude"
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if not appdata:
            return None
        user_data = Path(appdata) / "Claude"
    elif sys.platform.startswith("linux"):
        config_home = os.environ.get("XDG_CONFIG_HOME")
        user_data = Path(config_home) / "Claude" if config_home else Path.home() / ".config" / "Claude"
    else:
        return None
    return user_data / "local-agent-mode-sessions"


def find_audit_files(base_dir=None):
    """Return audit.jsonl files below the Cowork sessions directory."""
    base = Path(base_dir) if base_dir is not None else cowork_sessions_dir()
    if base is None or not base.exists():
        return []
    return sorted(base.rglob("audit.jsonl"))


def is_audit_file(filepath):
    """Return True when filepath looks like a Cowork audit log."""
    return Path(filepath).name == "audit.jsonl"


def normalize_model_name(model):
    """Strip Cowork tier hints like [1m] so pricing lookup still matches."""
    return re.sub(r"\[[^\]]+\]$", "", model or "")


def _session_meta(session_id, timestamp):
    short_id = session_id[:8] if session_id else "unknown"
    return {
        "session_id": session_id,
        "project_name": f"Cowork/{short_id}",
        "first_timestamp": timestamp,
        "last_timestamp": timestamp,
        "git_branch": "",
        "model": None,
        "custom_title": None,
        "agent_name": None,
    }


def parse_audit_file(filepath):
    """Parse a Cowork audit.jsonl file.

    Returns (session_metas, turns, line_count), matching scanner.parse_jsonl_file.
    Cowork result events contain cumulative authoritative modelUsage totals, so
    the last result event per session is used.
    """
    latest_results = {}
    line_count = 0

    try:
        with open(filepath, encoding="utf-8", errors="replace") as f:
            for line_count, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("type") != "result":
                    continue
                session_id = record.get("session_id")
                model_usage = record.get("modelUsage")
                if not session_id or not isinstance(model_usage, dict):
                    continue
                latest_results[session_id] = record
    except Exception as e:
        print(f"  Warning: error reading {filepath}: {e}")

    session_metas = []
    turns = []
    for session_id, record in latest_results.items():
        timestamp = record.get("_audit_timestamp") or record.get("timestamp") or ""
        session_metas.append(_session_meta(session_id, timestamp))

        totals_by_model = {}
        for raw_model, usage in record.get("modelUsage", {}).items():
            if not isinstance(usage, dict):
                continue
            model = normalize_model_name(raw_model)
            if not model:
                continue
            totals = totals_by_model.setdefault(model, {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
            })
            totals["input_tokens"] += usage.get("inputTokens", 0) or 0
            totals["output_tokens"] += usage.get("outputTokens", 0) or 0
            totals["cache_read_tokens"] += usage.get("cacheReadInputTokens", 0) or 0
            totals["cache_creation_tokens"] += usage.get("cacheCreationInputTokens", 0) or 0

        for model, totals in totals_by_model.items():
            if sum(totals.values()) == 0:
                continue
            turns.append({
                "session_id": session_id,
                "timestamp": timestamp,
                "model": model,
                "input_tokens": totals["input_tokens"],
                "output_tokens": totals["output_tokens"],
                "cache_read_tokens": totals["cache_read_tokens"],
                "cache_creation_tokens": totals["cache_creation_tokens"],
                "tool_name": None,
                "cwd": "",
                "message_id": f"cowork:{session_id}:{model}",
            })

    return session_metas, turns, line_count
