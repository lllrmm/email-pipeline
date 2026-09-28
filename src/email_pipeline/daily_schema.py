"""Strict validation for the public daily email summary schema."""

from __future__ import annotations

from typing import Any


DAILY_KEYS = {
    "date", "overview", "events", "warnings",
    "messages_total", "messages_requiring_review",
}
EVENT_KEYS = {
    "kind", "title", "start", "end", "due", "timezone", "location",
    "priority", "confidence", "source_messages",
}
SOURCE_KEYS = {"folder", "id", "subject"}


def validate_daily_summary(value: dict[str, Any]) -> None:
    if set(value) != DAILY_KEYS:
        raise ValueError(f"daily summary keys do not match schema: {sorted(value)}")
    if not isinstance(value.get("date"), str):
        raise ValueError("date must be a string")
    if not isinstance(value.get("overview"), str):
        raise ValueError("overview must be a string")
    if not isinstance(value.get("events"), list) or not isinstance(value.get("warnings"), list):
        raise ValueError("events and warnings must be arrays")
    if not all(isinstance(item, str) for item in value["warnings"]):
        raise ValueError("every warning must be a string")
    if not isinstance(value.get("messages_total"), int) or not isinstance(value.get("messages_requiring_review"), int):
        raise ValueError("message counts must be integers")
    for event in value["events"]:
        if not isinstance(event, dict) or set(event) != EVENT_KEYS:
            raise ValueError("event keys do not match schema")
        if event.get("kind") not in {"scheduled", "deadline", "action"}:
            raise ValueError(f"invalid event kind: {event.get('kind')}")
        if event.get("priority") not in {"high", "medium", "low"}:
            raise ValueError(f"invalid priority: {event.get('priority')}")
        if event.get("confidence") not in {"high", "medium", "low"}:
            raise ValueError(f"invalid confidence: {event.get('confidence')}")
        if not isinstance(event.get("source_messages"), list):
            raise ValueError("source_messages must be an array")
        for source in event["source_messages"]:
            if not isinstance(source, dict) or set(source) != SOURCE_KEYS:
                raise ValueError("source message keys do not match schema")


def validation_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"valid": False, "errors": ["root must be a JSON object"]}
    try:
        validate_daily_summary(value)
    except ValueError as exc:
        return {"valid": False, "errors": [str(exc)]}
    return {"valid": True, "errors": []}
