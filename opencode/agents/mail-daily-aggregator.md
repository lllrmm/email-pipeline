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
