"""Append-only run log (verifications.log.jsonl).

Every run and every human review action is appended as a JSON line. Nothing is
rewritten in place — this is the audit trail the workflow record requires (B5:
"each run is logged; human signal drives the next iteration").
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunLog:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: dict[str, Any]) -> None:
        """Append one event, tagged with an iso timestamp if absent."""
        record = dict(event)
        record.setdefault("ts", _now())
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")

    def read(self) -> list[dict[str, Any]]:
        """Return all events, in order. Missing file -> empty list."""
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events
