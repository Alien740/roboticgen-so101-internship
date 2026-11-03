"""Robot profile: all machine/robot-specific settings live in profiles/<name>/robot.yaml.

Selection order: explicit name -> $SO101_PROFILE -> "my_so101".
"""
from __future__ import annotations

import copy
import datetime as _dt
import os
from pathlib import Path

import yaml

FRAMEWORK_ROOT = Path(__file__).resolve().parent.parent
PROFILES_DIR = FRAMEWORK_ROOT / "profiles"
RUNS_DIR = FRAMEWORK_ROOT / "runs"


class Profile(dict):
    """dict with attribute access for top-level sections, plus path helpers."""

    def __init__(self, name: str, data: dict, folder: Path):
        super().__init__(data)
        self.name = name
        self.folder = folder

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as e:
            raise AttributeError(key) from e

    def path(self, rel: str) -> Path:
        """Resolve a path stored in the profile relative to the profile folder."""
        p = Path(rel)
        return p if p.is_absolute() else self.folder / p

    @property
    def run_dir(self) -> Path:
        d = RUNS_DIR / self.name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save_measured(self, section: str, values: dict):
        """Persist measured quirks to measured.yaml (kept separate so robot.yaml's comments,
        which document WHY each value is set, are never rewritten)."""
        f = self.folder / "measured.yaml"
        raw = (yaml.safe_load(f.read_text()) if f.exists() else None) or {}
        raw[section] = {**values, "at": _dt.datetime.now().isoformat(timespec="seconds")}
        f.write_text(yaml.safe_dump(raw, sort_keys=False))
        self["measured"] = raw


def list_profiles() -> list[str]:
    return sorted(p.name for p in PROFILES_DIR.iterdir() if (p / "robot.yaml").exists())


def load(name: str | None = None) -> Profile:
    name = name or os.environ.get("SO101_PROFILE") or "my_so101"
    folder = PROFILES_DIR / name
    f = folder / "robot.yaml"
    if not f.exists():
        raise FileNotFoundError(f"profile '{name}' not found ({f}). Available: {list_profiles()}")
    data = yaml.safe_load(f.read_text()) or {}
    m = folder / "measured.yaml"
    data["measured"] = (yaml.safe_load(m.read_text()) if m.exists() else None) or {}
    return Profile(name, copy.deepcopy(data), folder)
