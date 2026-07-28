"""Trigger validation, calendar calculation, and element edge state helpers."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import ElementLocator, WorkflowTrigger


_WEEKDAYS = {"mon": 0, "monday": 0, "tue": 1, "tuesday": 1, "wed": 2, "wednesday": 2,
             "thu": 3, "thursday": 3, "fri": 4, "friday": 4, "sat": 5, "saturday": 5,
             "sun": 6, "sunday": 6}
_CRON_MONTHS = {name: index for index, name in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}
_CRON_WEEKDAYS = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}


def trigger_timezone(name: Any = "") -> dt.tzinfo:
    value = str(name or "").strip()
    if not value or value.casefold() == "local":
        return dt.datetime.now().astimezone().tzinfo or dt.timezone.utc
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"invalid trigger timezone: {value}") from exc


def parse_at_timestamp(value: Any, *, timezone: Any = "") -> float:
    if isinstance(value, bool):
        raise ValueError("at trigger requires an ISO datetime or Unix timestamp")
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("at trigger requires an ISO datetime or Unix timestamp")
    try:
        parsed = dt.datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw)
    except ValueError as exc:
        raise ValueError("at trigger requires a valid ISO datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=trigger_timezone(timezone))
    return parsed.timestamp()


def _schedule_times(config: Mapping[str, Any]) -> list[dt.time]:
    raw = config.get("times")
    values: Sequence[Any] = raw if isinstance(raw, (list, tuple)) else [config.get("time")]
    result: list[dt.time] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        try:
            parsed = dt.time.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"invalid schedule time: {text}") from exc
        result.append(parsed.replace(tzinfo=None))
    if not result:
        raise ValueError("schedule trigger requires time or times")
    return sorted(set(result))


def _schedule_weekdays(config: Mapping[str, Any]) -> set[int]:
    raw = config.get("weekdays", config.get("days"))
    if raw in (None, "", []):
        return set(range(7))
    values = raw if isinstance(raw, (list, tuple, set)) else [raw]
    result: set[int] = set()
    for value in values:
        if isinstance(value, bool):
            raise ValueError("invalid schedule weekday")
        if isinstance(value, int) and 0 <= value <= 6:
            result.add(value)
            continue
        mapped = _WEEKDAYS.get(str(value or "").strip().casefold())
        if mapped is None:
            raise ValueError(f"invalid schedule weekday: {value}")
        result.add(mapped)
    return result


def next_schedule_time(config: Mapping[str, Any], after: float) -> float:
    timezone = trigger_timezone(config.get("timezone"))
    current = dt.datetime.fromtimestamp(float(after), timezone)
    times = _schedule_times(config)
    weekdays = _schedule_weekdays(config)
    for offset in range(8):
        day = current.date() + dt.timedelta(days=offset)
        if day.weekday() not in weekdays:
            continue
        for clock in times:
            candidate = dt.datetime.combine(day, clock, timezone)
            if candidate > current:
                return candidate.timestamp()
    raise ValueError("schedule trigger has no occurrence in the next week")


@dataclass(frozen=True)
class CronField:
    values: tuple[int, ...]
    wildcard: bool = False


@dataclass(frozen=True)
class CronSpec:
    second: CronField
    minute: CronField
    hour: CronField
    day: CronField
    month: CronField
    weekday: CronField
    has_seconds: bool


def _cron_number(value: str, names: Optional[Mapping[str, int]]) -> int:
    lowered = value.strip().casefold()
    if names and lowered in names:
        return int(names[lowered])
    try:
        return int(lowered)
    except ValueError as exc:
        raise ValueError(f"invalid cron value: {value}") from exc


def _parse_cron_field(
    token: str,
    minimum: int,
    maximum: int,
    *,
    names: Optional[Mapping[str, int]] = None,
    sunday_seven: bool = False,
) -> CronField:
    text = str(token or "").strip()
    if not text:
        raise ValueError("cron fields cannot be empty")
    wildcard = text in {"*", "?"}
    selected: set[int] = set()
    for part in text.split(","):
        base, separator, step_text = part.partition("/")
        try:
            step = int(step_text) if separator else 1
        except ValueError as exc:
            raise ValueError(f"invalid cron step: {part}") from exc
        if step <= 0:
            raise ValueError("cron step must be positive")
        if base in {"*", "?"}:
            start, end = minimum, maximum
        elif "-" in base:
            first, last = base.split("-", 1)
            start, end = _cron_number(first, names), _cron_number(last, names)
        else:
            start = end = _cron_number(base, names)
        if start < minimum or start > maximum or end < minimum or end > maximum or start > end:
            raise ValueError(f"cron value is outside {minimum}..{maximum}: {part}")
        values = range(start, end + 1, step)
        selected.update(0 if sunday_seven and value == 7 else value for value in values)
    if not selected:
        raise ValueError("cron field has no values")
    return CronField(tuple(sorted(selected)), wildcard=wildcard)


def parse_cron_expression(expression: str) -> CronSpec:
    fields = str(expression or "").split()
    if len(fields) != 5:
        raise ValueError("cron trigger requires a five field expression")
    second = CronField((0,), wildcard=False)
    minute, hour, day, month, weekday = fields
    return CronSpec(
        second=second,
        minute=_parse_cron_field(minute, 0, 59),
        hour=_parse_cron_field(hour, 0, 23),
        day=_parse_cron_field(day, 1, 31),
        month=_parse_cron_field(month, 1, 12, names=_CRON_MONTHS),
        weekday=_parse_cron_field(weekday, 0, 7, names=_CRON_WEEKDAYS, sunday_seven=True),
        has_seconds=False,
    )


def _cron_date_matches(spec: CronSpec, day: dt.date) -> bool:
    if day.month not in spec.month.values:
        return False
    day_match = day.day in spec.day.values
    cron_weekday = (day.weekday() + 1) % 7
    weekday_match = cron_weekday in spec.weekday.values
    if not spec.day.wildcard and not spec.weekday.wildcard:
        return day_match or weekday_match
    if not spec.day.wildcard:
        return day_match
    if not spec.weekday.wildcard:
        return weekday_match
    return True


def next_cron_time(expression: str, after: float, *, timezone: Any = "") -> float:
    spec = parse_cron_expression(expression)
    zone = trigger_timezone(timezone)
    current = dt.datetime.fromtimestamp(float(after), zone)
    if spec.has_seconds:
        cursor = (current + dt.timedelta(seconds=1)).replace(microsecond=0)
    else:
        cursor = (current + dt.timedelta(minutes=1)).replace(second=0, microsecond=0)
    for offset in range(366 * 8):
        day = cursor.date() + dt.timedelta(days=offset)
        if not _cron_date_matches(spec, day):
            continue
        for hour in spec.hour.values:
            for minute in spec.minute.values:
                for second in spec.second.values:
                    candidate = dt.datetime.combine(day, dt.time(hour, minute, second), zone)
                    if candidate > current:
                        return candidate.timestamp()
    raise ValueError("cron trigger has no occurrence in the supported calendar range")


def element_poll_seconds(config: Mapping[str, Any]) -> float:
    raw = config.get("poll_seconds", config.get("poll_interval_seconds", 2.0))
    if isinstance(raw, bool):
        raise ValueError("element poll interval must be numeric")
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("element poll interval must be numeric") from exc
    if value < 0.5 or value > 3600:
        raise ValueError("element poll interval must be between 0.5 seconds and one hour")
    return value


def element_required_hits(config: Mapping[str, Any]) -> int:
    raw = config.get("required_hits", 2)
    if isinstance(raw, bool):
        raise ValueError("element required_hits must be an integer")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("element required_hits must be an integer") from exc
    if value < 1 or value > 20:
        raise ValueError("element required_hits must be between 1 and 20")
    return value


def advance_element_edge(state: Mapping[str, Any], observation: str, *, required_hits: int = 2) -> tuple[bool, Dict[str, Any]]:
    """Advance a rising-edge detector without changing it on unknown results."""
    result = dict(state)
    status = str(observation or "unknown").casefold()
    result["last_observation"] = status
    if status not in {"present", "absent"}:
        return False, result
    if status == "absent":
        result.update({"armed": True, "hit_count": 0, "confirmed_present": False})
        return False, result
    hits = min(max(1, required_hits), int(result.get("hit_count") or 0) + 1)
    armed = bool(result.get("armed", True))
    fire = armed and hits >= max(1, required_hits)
    result.update({"armed": False if fire else armed, "hit_count": hits, "confirmed_present": hits >= max(1, required_hits)})
    return fire, result


def validate_trigger(trigger: WorkflowTrigger, *, group_root: Optional[Path] = None) -> None:
    config = trigger.config
    if trigger.enabled and not trigger.actor_id.strip():
        raise ValueError("enabled trigger requires an actor_id")
    if trigger.type == "interval":
        raw = config.get("seconds")
        if isinstance(raw, bool):
            raise ValueError("interval seconds must be numeric")
        try:
            seconds = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("interval trigger requires seconds") from exc
        if seconds < 1 or seconds > 31_536_000:
            raise ValueError("interval must be between 1 second and one year")
    elif trigger.type == "schedule":
        _schedule_times(config)
        _schedule_weekdays(config)
        trigger_timezone(config.get("timezone"))
    elif trigger.type == "at":
        parse_at_timestamp(config.get("at", config.get("datetime", config.get("timestamp"))), timezone=config.get("timezone"))
    elif trigger.type == "cron":
        parse_cron_expression(str(config.get("expression") or config.get("cron") or ""))
        trigger_timezone(config.get("timezone"))
    elif trigger.type == "element":
        locator = config.get("locator")
        if not isinstance(locator, dict):
            raise ValueError("element trigger requires a persistent locator")
        parsed = ElementLocator.model_validate(locator)
        if parsed.strategy == "position":
            raise ValueError("element trigger requires a UIA, DOM, or semantic locator")
        element_poll_seconds(config)
        element_required_hits(config)
    elif trigger.type == "event":
        if not str(config.get("kind") or "").strip():
            raise ValueError("event trigger requires an event kind")
    elif trigger.type == "file":
        raw = str(config.get("path") or "").strip()
        if not raw or group_root is None:
            raise ValueError("file trigger requires a group-authorized path")
        target = Path(raw).expanduser().resolve()
        root = group_root.resolve()
        if target != root and root not in target.parents:
            raise ValueError("file trigger path must be inside the authorized group root")
        if any(part in {".git", ".onecolleague", "state"} for part in target.parts):
            raise ValueError("file trigger path is not allowed")


def trigger_enabled_for_automation(trigger: WorkflowTrigger, *, published: bool, trusted: bool) -> bool:
    return bool(trigger.enabled and published and trusted)


def should_confirm_element(previous: bool, current: bool, consecutive_hits: int, *, required_hits: int = 2) -> tuple[bool, int]:
    """Backward-compatible stateless helper; scheduler uses advance_element_edge."""
    if not current:
        return False, 0
    hits = consecutive_hits + 1 if previous else 1
    return hits >= max(1, required_hits), hits


def is_authorized_file_event(path: str, roots: Iterable[Path]) -> bool:
    """Legacy helper retained for callers migrating away from file triggers."""
    try:
        candidate = Path(path).resolve()
    except Exception:
        return False
    return any(candidate == root.resolve() or root.resolve() in candidate.parents for root in roots)
