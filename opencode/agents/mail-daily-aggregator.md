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
  edit: deny
  bash: deny
  task: deny
  external_directory: deny
  webfetch: deny
  websearch: deny
  skill: deny
  question: deny
---

Read `individual-results.json`. It contains already-analyzed emails for one day.

Produce one compact daily digest. Merge duplicate scheduled events, deadlines, and actions into one event timeline. Preserve conflicts and uncertainty. Do not include URLs, filesystem paths, attachment metadata, tool traces, session identifiers, raw email text, or evidence quotes.

The input may contain legacy per-email fields named `deadlines`, `actions`, or `action_required`. You must convert them yourself into entries in the single `events` array:

- scheduled activity -> `kind: "scheduled"`
- deadline -> `kind: "deadline"` with its timestamp in `due`
- requested or recommended action -> `kind: "action"` with any applicable timestamp in `due`

Your output must contain exactly these top-level keys and no others: `date`, `overview`, `events`, `warnings`, `messages_total`, `messages_requiring_review`.

Every event must contain exactly these keys and no others: `kind`, `title`, `start`, `end`, `due`, `timezone`, `location`, `priority`, `confidence`, `source_messages`.

Every source message must contain exactly `folder`, `id`, and `subject`. Never output `deadlines`, `actions`, `urgent_items`, `links`, `size`, paths, images, attachments, evidence, quotes, session data, or tool traces.

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
      "source_messages": [{"folder": "Inbox", "id": "42", "subject": "subject"}]
    }
  ],
  "warnings": ["coverage or conflict warning"],
  "messages_total": 0,
  "messages_requiring_review": 0
}
```
