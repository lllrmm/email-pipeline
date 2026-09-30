# Email Pipeline

Read-only Outlook pipeline using IMAPClient, SQLite, and isolated OpenCode sessions.

## Commands

Initialize an instance in the current working directory:

```bash
email-pipeline init
```

This initializes the current directory itself. Its `instance.toml`, `config/`,
`auth/`, database, logs, locks, and workspaces are all instance-local. Email
OAuth tokens and refresh scripts belong in `auth/`. There is no
machine-wide instance registry.
Run all subsequent commands from the initialized directory; multiple directories
can run independently on the same machine.

All operations use one entry point:

```bash
email-pipeline watch
email-pipeline consume
email-pipeline scan --from-time <RFC3339> --to-time <RFC3339>
email-pipeline scan-enqueue --date YYYY-MM-DD
email-pipeline summarize --pipeline-id ID --mail-dir DIR --output DIR/summary.json
email-pipeline aggregate-day --date YYYY-MM-DD
email-pipeline lookup --pipeline-id ID
```

The code launcher is installed under `~/email-pipeline-code/`; each initialized
working directory owns its configuration and runtime data directly in that directory.

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
~/email-pipeline/daily/YYYY-MM-DD/
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
