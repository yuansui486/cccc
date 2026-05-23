"""Workflow JSON loading and parameter injection."""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class WorkflowConfig:
    workflow_file: Path
    image_node_id: str
    audio_node_id: str
    video_node_id: str
    width_node_id: str
    height_node_id: str


class WorkflowError(Exception):
    pass


class Workflow:
    def __init__(self, cfg: WorkflowConfig):
        self.cfg = cfg
        if not cfg.workflow_file.exists():
            raise WorkflowError(f"workflow file not found: {cfg.workflow_file}")
        with cfg.workflow_file.open("r", encoding="utf-8") as f:
            self._template = json.load(f)

    def build(self, image_name: str, audio_name: str, width: int, height: int) -> dict:
        p = copy.deepcopy(self._template)
        try:
            p[self.cfg.image_node_id]["inputs"]["image"] = image_name
            p[self.cfg.audio_node_id]["inputs"]["audio"] = audio_name
            p[self.cfg.width_node_id]["inputs"]["value"] = int(width)
            p[self.cfg.height_node_id]["inputs"]["value"] = int(height)
        except KeyError as exc:
            raise WorkflowError(f"workflow missing node {exc}") from exc
        return p
