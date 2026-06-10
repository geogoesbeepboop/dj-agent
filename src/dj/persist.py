"""Persist generated set plans + a played-track history (Phase 5/6 substrate).

A SetPlan is plain data (`plan.to_dict()`), so each generated set is appended to a
JSON-lines history in `DJ_OUTPUT_DIR` with the brief, a timestamp, and the approval
verdict. That one file gives three things at once:
  - a record of what was proposed/approved — the raw data behind the
    **set-acceptance** eval and the Phase-6 memory loop, and
  - the **recently-played** track list the Selector excludes, so back-to-back
    sessions don't keep replaying the same handful of favorites (#24).

Plain JSON on disk — no DB — so it works the moment a set is generated.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from dj.config import settings
from dj.plan import SetPlan

_HISTORY = "set_history.jsonl"


def history_path(out_dir: str | None = None) -> Path:
    return Path(out_dir or settings.output_dir) / _HISTORY


def save_plan(
    plan: SetPlan,
    *,
    brief: str = "",
    approved: bool = False,
    timestamp: float | None = None,
    out_dir: str | None = None,
) -> str:
    """Append one generated set to the rolling history. Returns the history path."""
    hist = history_path(out_dir)
    hist.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "brief": brief,
        "approved": approved,
        "captured_at": timestamp if timestamp is not None else time.time(),
        "paths": plan.paths,
        "plan": plan.to_dict(),
    }
    with hist.open("a") as f:
        f.write(json.dumps(record) + "\n")
    return str(hist)


def load_history(out_dir: str | None = None) -> list[dict]:
    """Every generated set, oldest first (empty if nothing's been generated yet)."""
    hist = history_path(out_dir)
    if not hist.exists():
        return []
    return [json.loads(line) for line in hist.read_text().splitlines() if line.strip()]


def recent_paths(*, within: int = 3, approved_only: bool = False, out_dir: str | None = None) -> set[str]:
    """Track paths used in the last `within` sets — the Selector demotes/excludes these."""
    records = load_history(out_dir)
    if approved_only:
        records = [r for r in records if r.get("approved")]
    paths: set[str] = set()
    for r in records[-within:]:
        paths.update(r.get("paths", []))
    return paths
