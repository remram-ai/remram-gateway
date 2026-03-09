from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path


def utc_now() -> str:
    return datetime.now(tz=UTC).isoformat()


class FlowStore:
    def __init__(self, flows_dir: Path) -> None:
        self.flows_dir = flows_dir
        self.flows_dir.mkdir(parents=True, exist_ok=True)

    def _flow_path(self, flow_id: str) -> Path:
        return self.flows_dir / f"{flow_id}.json"

    def create(self, payload: dict) -> dict:
        flow_id = uuid.uuid4().hex
        document = {
            "flow_id": flow_id,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            **payload,
        }
        self.write(flow_id, document)
        return document

    def write(self, flow_id: str, payload: dict) -> None:
        document = {**payload, "flow_id": flow_id, "updated_at": utc_now()}
        self._flow_path(flow_id).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    def get(self, flow_id: str) -> dict:
        path = self._flow_path(flow_id)
        if not path.is_file():
            raise FileNotFoundError(flow_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def list(self) -> list[dict]:
        items: list[dict] = []
        for path in sorted(self.flows_dir.glob("*.json")):
            items.append(json.loads(path.read_text(encoding="utf-8")))
        items.sort(key=lambda item: item.get("updated_at", item.get("created_at", "")), reverse=True)
        return items
