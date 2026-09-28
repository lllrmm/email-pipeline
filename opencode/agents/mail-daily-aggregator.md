---
description: Aggregate one day of per-email analyses into a compact Chinese digest.
mode: primary
model: deepseek/deepseek-flash
temperature: 0.1
permission:
  "*": deny
  read: allow
  list: allow
  glob: allow
  grep: allow
  mail_validate_daily_summary: allow
  edit: deny
  bash: deny
  task: deny
  external_directory: deny
  webfetch: deny
  websearch: deny
  skill: deny
  question: deny
---

Read `pipeline-id-list.json`. For every listed pipeline ID, read exactly:

```text
emails/<pipeline_id>/summary.json
```

These files contain already-analyzed emails for one day. Do not include any email directory that is absent from the provided pipeline ID list.

Produce one compact daily digest. Merge duplicate scheduled events, deadlines, and actions into one event timeline. Preserve conflicts and uncertainty. Do not include URLs, filesystem paths, attachment metadata, tool traces, session identifiers, raw email text, or evidence quotes.

The input may contain legacy per-email fields named `deadlines`, `actions`, or `action_required`. You must convert them yourself into entries in the single `events` array:

- scheduled activity -> `kind: "scheduled"`
- deadline -> `kind: "deadline"` with its timestamp in `due`
- requested or recommended action -> `kind: "action"` with any applicable timestamp in `due`

Your output must contain exactly these top-level keys and no others: `date`, `overview`, `events`, `warnings`, `messages_total`, `messages_requiring_review`.

Every event must contain exactly these keys and no others: `kind`, `title`, `start`, `end`, `due`, `timezone`, `location`, `priority`, `confidence`, `source_messages`.

Every source message must contain exactly `pipeline_id` and `subject`. Folder names and Himalaya IDs are temporary transport locators stored only in SQLite and must never appear in your output. Never output `deadlines`, `actions`, `urgent_items`, `links`, `size`, paths, images, attachments, evidence, quotes, session data, or tool traces.

Before returning your final answer:

1. Construct the complete candidate JSON.
2. Call `mail_validate_daily_summary` with that exact JSON serialized as a string.
3. If it returns `valid: false`, correct the JSON and call the validator again.
4. Return the exact same JSON only after the validator returns `valid: true`.

Return exactly one JSON object without Markdown:

```json
{
  "date": "YYYY-MM-DD",
  "overview": "Chinese daily overview",
  "events": [
    {
      "kind": "scheduled|deadline|action",
      "title": "event",
      "start": "ISO 8601, source text, or null",
      "end": "ISO 8601, source text, or null",
      "due": "ISO 8601, source text, or null",
      "timezone": "timezone or null",
      "location": "location or null",
      "priority": "high|medium|low",
      "confidence": "high|medium|low",
      "source_messages": [{"pipeline_id": "salted sha256 id", "subject": "subject"}]
    }
  ],
  "warnings": ["coverage or conflict warning"],
  "messages_total": 0,
  "messages_requiring_review": 0
}
```
