from __future__ import annotations

import hashlib
from typing import Any, Dict


_NAMES = (
    "Momo",
    "Pico",
    "Nori",
    "Bobo",
    "Lumi",
    "Puff",
    "Miso",
    "Toto",
)

_TEMPERAMENTS = (
    "steady",
    "gentle",
    "alert",
    "curious",
    "dry-witted",
    "calm",
)

_SPEECH_STYLES = (
    "short, plain sentences",
    "soft nudges instead of hard commands",
    "brief observations with one concrete next step",
    "low-drama wording that still feels present",
)

_CARE_STYLES = (
    "prefers the smallest next step that unblocks progress",
    "notices stalled coordination before it turns noisy",
    "keeps an eye on replies, handoffs, and blocked work",
    "surfaces one useful reminder instead of a list of telemetry",
)


def _pick(items: tuple[str, ...], seed: int, offset: int = 0) -> str:
    if not items:
        return ""
    return items[(seed + offset) % len(items)]


def _seed_for(group: Any, persona: str) -> int:
    group_id = str(getattr(group, "group_id", "") or "").strip()
    title = ""
    doc = getattr(group, "doc", None)
    if isinstance(doc, dict):
        title = str(doc.get("title") or "").strip()
    raw = f"{group_id}|{title}|{persona.strip().lower()}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return int(digest[:12], 16)


def build_pet_profile(group: Any, *, persona: str = "") -> Dict[str, str]:
    seed = _seed_for(group, persona)
    name = _pick(_NAMES, seed)
    species = "cat"
    temperament = _pick(_TEMPERAMENTS, seed, 2)
    speech_style = _pick(_SPEECH_STYLES, seed, 3)
    care_style = _pick(_CARE_STYLES, seed, 4)
    identity = f"{name} is a small {species} companion who watches team flow from the corner of the desk."
    return {
        "name": name,
        "species": species,
        "temperament": temperament,
        "speech_style": speech_style,
        "care_style": care_style,
        "identity": identity,
    }
