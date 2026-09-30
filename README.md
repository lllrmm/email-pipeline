# Email Pipeline

Read-only Outlook pipeline using IMAPClient, SQLite, and isolated OpenCode sessions.

## Commands

Initialize an instance in the current working directory:

```bash
email-pipeline init
```

This initializes the current directory itself. Its `instance.toml`, `config/`,
database, logs, locks, and workspaces are all instance-local. Email OAuth tokens
live in the instance-root `auth.sqlite3`, refreshed by `outlook-token-refresh.py`
next to it. There is no machine-wide instance registry.
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
email-pipeline service start
email-pipeline service restart
email-pipeline service status
```

The package is installed into `~/email-pipeline-code/.venv/` as the `email-pipeline`
console script (symlinked at `~/.local/bin/email-pipeline`); each initialized working
directory owns its configuration and runtime data directly in that directory.

## Package Layout

```text
src/email_pipeline/
├── __main__.py              unified command dispatcher
├── cli/                     command shells and orchestration
│   ├── scan.py
│   ├── enqueue_scan.py
│   ├── watch.py
│   ├── consume.py
│   ├── summarize.py         argparse shell over summarizer/
│   ├── aggregate.py         argparse shell over aggregator/
│   ├── daily_aggregation.py wait for a day's queue, then run aggregate
│   └── lookup.py
├── summarizer/
│   └── summarize.py         single-email OpenCode analysis logic
├── aggregator/
│   ├── aggregate.py         daily aggregation logic
│   └── daily_schema.py      aggregation output validation
├── services/
│   └── registry.py          stable identity and IMAP-location registration
├── mail_identity.py         SQLite repositories and schema migration
├── imap_backend.py          read-only IMAP transport
├── agent_tools.py           restricted OpenCode mail tools
├── mime_extract.py          MIME, HTML, attachment, and link extraction
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
python3 -m compileall -q src/email_pipeline
```
