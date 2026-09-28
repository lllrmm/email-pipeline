# Email Pipeline

Read-only Outlook pipeline using IMAPClient, SQLite, and isolated OpenCode sessions.

## Commands

All operations use one entry point:

```bash
python3 email-pipeline.py watch
python3 email-pipeline.py consume
python3 email-pipeline.py scan --from-time <RFC3339> --to-time <RFC3339>
python3 email-pipeline.py scan-enqueue --date YYYY-MM-DD
python3 email-pipeline.py summarize --pipeline-id ID --mail-dir DIR --output DIR/summary.json
python3 email-pipeline.py aggregate-day --date YYYY-MM-DD
python3 email-pipeline.py lookup --pipeline-id ID
```

The installed runtime is `~/.hermes/scripts/email-pipeline/`. Configuration is
`daily-mail-pipeline.toml`; summarizer and aggregator prompts are separate text files.

## Package Layout

```text
src/email_pipeline/
├── __main__.py              unified command dispatcher
├── cli/                     command adapters
│   ├── scan.py
│   ├── enqueue_scan.py
│   ├── watch.py
│   ├── consume.py
│   ├── summarize.py
│   ├── aggregate.py
│   ├── daily_aggregation.py
│   └── lookup.py
├── services/
│   └── registry.py          stable identity and IMAP-location registration
├── mail_identity.py         SQLite repositories and schema migration
├── imap_backend.py          read-only IMAP transport
├── agent_tools.py           restricted OpenCode mail tools
├── mime_extract.py          MIME, HTML, attachment, and link extraction
├── daily_schema.py          aggregation output validation
├── daily_logging.py         date-partitioned logs
├── program_time.py          configured RFC3339 program time
└── config.py                TOML and stage-prompt loading
```

## Runtime Flow

```text
watch / scan-enqueue
  -> email_event_queue
  -> consume
  -> registry + stable workspace
  -> summarize
  -> summary.json

aggregate-day
  -> wait for the date queue to finish
  -> aggregate
  -> aggregation-<timestamp>.json
```

SQLite has four business tables: `email`, `email_location`,
`email_event_queue`, and `email_watch_folder_state`.

## Data Layout

```text
~/.hermes/email/daily/YYYY-MM-DD/
├── emails/<pipeline_id>/
│   ├── request.json
│   ├── message.eml
│   ├── manifest.json
│   ├── summary.json
│   └── opencode-run/
├── logs/
│   ├── watcher/watcher.log
│   └── scanner/scanner.log
└── aggregation/
    └── aggregation-<timestamp>.json
```

## Test

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m compileall -q src/email_pipeline email-pipeline.py
```
