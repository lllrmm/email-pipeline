"""Strict validation for the public daily email summary schema."""

from __future__ import annotations

from typing import Any


DAILY_KEYS = {
    "date", "overview", "events", "warnings",
    "messages_total", "messages_requiring_review",
}
EVENT_KEYS = {
    "title", "scheduled", "deadlines", "actions", "priority", "confidence",
    "source_messages", "warnings",
}
SCHEDULE_KEYS = {"content", "start", "end", "timezone", "location", "confidence"}
DEADLINE_KEYS = {"title", "content", "due", "timezone", "priority", "confidence"}
ACTION_KEYS = {"title", "content", "due", "priority", "confidence"}
SOURCE_KEYS = {"pipeline_id", "subject"}


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
        if event.get("priority") not in {"high", "medium", "low"}:
            raise ValueError(f"invalid priority: {event.get('priority')}")
        if event.get("confidence") not in {"high", "medium", "low"}:
            raise ValueError(f"invalid confidence: {event.get('confidence')}")
        if not isinstance(event.get("warnings"), list) or not all(isinstance(item, str) for item in event["warnings"]):
            raise ValueError("event warnings must be an array of strings")
        for field, keys in (
            ("scheduled", SCHEDULE_KEYS),
            ("deadlines", DEADLINE_KEYS),
            ("actions", ACTION_KEYS),
        ):
            if not isinstance(event.get(field), list):
                raise ValueError(f"{field} must be an array")
            for item in event[field]:
                if not isinstance(item, dict) or set(item) != keys:
                    raise ValueError(f"{field} item keys do not match schema")
                if not isinstance(item.get("content"), str) or not item["content"].strip():
                    raise ValueError(f"{field} content must be a non-empty string")
                if item.get("confidence") not in {"high", "medium", "low"}:
                    raise ValueError(f"invalid {field} confidence: {item.get('confidence')}")
                if "priority" in keys and item.get("priority") not in {"high", "medium", "low"}:
                    raise ValueError(f"invalid {field} priority: {item.get('priority')}")
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
