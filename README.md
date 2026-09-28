# Email Pipeline

Read-only Outlook IMAPClient email extraction and digest pipeline for Hermes Agent.

All program-controlled timestamps are RFC3339 values in the timezone configured
by the top-level `timezone` in `daily-mail-pipeline.toml`. API ranges accept any
timezone-aware RFC3339 values and compare them as absolute instants. Times
authored inside email headers, bodies, attachments, or linked pages are not
normalized; they remain source material for the analysis agent.

## What it fixes

- Parses raw RFC 5322 messages instead of Himalaya's rendered terminal output.
- Selects meaningful `text/plain`, otherwise falls back to sanitized HTML.
- Extracts links and identifies images wrapped in clickable links.
- Records inline and attached images without loading remote image URLs.
- Saves private `.eml`, extracted text, attachment files, and manifests.
- Extracts text from TXT/CSV/JSON/ICS, PDF, DOCX, XLSX, and PPTX attachments.
- Blocks executable attachment types and enforces count and size limits.
- Keeps Outlook read-only: no SMTP, `--seen`, move, delete, or flag operation.

## Layout

```text
scan_mails.py                      Envelope scan + stable identity/workspace index
index_mail.py                      Register one RFC Message-ID and return pipeline_id/status
mail-index.py                      pipeline_id / RFC Message-ID lookup CLI
summarize-mail-agentic.py          One pipeline_id + workdir + JSON output
daily-mail-pipeline.py             Scan and enqueue fallback producer
consume_mail_queue.py              Durable queue consumer and summarizer
aggregate-mails-agentic.py         Aggregate an explicit pipeline ID list
get_daily_aggregation.py           Wait for one date's queue, then aggregate
src/email_pipeline/mime_extract.py MIME and attachment extraction library
daily-mail-pipeline.toml.example   Configuration without credentials
tests/                             Synthetic MIME regression tests
```

## Stage boundary

```text
Outlook envelope metadata
    -> scan_mails.py
    -> index_mail.py registers each RFC Message-ID
    -> scan-log/scan-<generated program time>.json
    -> durable email_event_queue
    -> consume_mail_queue.py registers and summarizes each pending email
    -> explicit aggregate-mails-agentic.py call
```

`scan_mails.py` accepts an aware RFC3339 time range and outputs the discovered
`rfc_message_id`, folder, UIDVALIDITY, and folder-scoped IMAP UID. `index_mail.py`
accepts one such triple, registers the stable identity and transport location,
and returns the `pipeline_id` with status `registered` or
`already_registered`. Neither script reads message bodies or unpacks MIME. The single-email
OpenCode agent owns `mail_fetch`, `mail_unpack`, attachment extraction, and
link inspection inside `emails/<pipeline_id>/`.

`daily-mail-pipeline.py` calls `index_mail.py` for every scanned triple, creates
the fixed workspace request, and then performs incremental summary planning
using the returned pipeline IDs. Only unsummarized IDs invoke
`summarize-mail-agentic.py`; aggregation still includes every unique pipeline
ID from the current scan.

## OpenCode single-email agent chain

`summarize-mail-agentic.py` provides a fully agent-driven path for one email. The entry
point writes only `request.json`; the OpenCode `mail-analyzer` session must call
restricted tools to fetch the message, unpack MIME, inspect attachments and
links, and return evidence-backed JSON.

```bash
python3 summarize-mail-agentic.py \
  --pipeline-id <pipeline_id> \
  --mail-dir ~/.hermes/email/daily/2026-09-25/emails/<pipeline_id> \
  --output ~/.hermes/email/daily/2026-09-25/emails/<pipeline_id>/summary.json \
  --config ~/.hermes/scripts/email-pipeline/daily-mail-pipeline.toml
```

The chain uses a dedicated OpenCode HOME/config/data directory and injects the
email-only DeepSeek credential into that process. It does not share the normal
OpenCode Web server database or provider credentials.

Available agent tools:

```text
mail_fetch
mail_unpack
mail_extract_attachment
mail_extract_links
mail_inspect_link
```

The agent has broad read/analysis capability inside its one-message workspace,
but no generic shell, external-directory access, mailbox mutation, or browser
session with cookies.

After all per-email sessions finish, `aggregate-mails-agentic.py` receives the
pipeline ID list, date work directory, and output path. The
`mail-daily-aggregator` reads each `emails/<pipeline_id>/summary.json`, groups
related activity times, deadlines, and actions into one real-world event, and
writes a timestamped public JSON under `aggregation/`. The public
summary and Cron stdout intentionally exclude raw mail
paths, EML paths, attachment manifests/text paths, sizes, extraction counters,
links, images, and attachment inventories.

Every per-email `summary.json` and daily aggregation JSON has a top-level
`generated_at` RFC3339 timestamp in the configured program timezone.
Each aggregation artifact also has a top-level `included_pipeline_ids`
array containing the complete validated, de-duplicated input list in order.
Python derives top-level `earliest_email_at` and `latest_email_at` timestamps
from those included messages; the aggregation agent does not generate them.

The public daily timeline uses one `events` array. Each event contains its own
`scheduled`, `deadlines`, and `actions` arrays. Every nested item has a required
non-empty `content` description, so an activity and its
registration deadline/action stay in the same event object. This JSON is
written exactly as returned by the daily aggregation agent. Python validates
the schema and rejects invalid/extra fields; it does not delete, add, rename,
or convert fields.

## Test

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m py_compile daily-mail-pipeline.py scan_mails.py index_mail.py summarize-mail-agentic.py src/email_pipeline/*.py
```

The test suite never connects to a mailbox or model API.

For a read-only fallback scan that only enqueues newly discovered messages:

```bash
python3 daily-mail-pipeline.py \
  --date 2026-09-27 \
  --output-root ~/.hermes/email/daily-v2-shadow
```

To revisit one indexed email, look it up by `pipeline_id` or RFC Message-ID and
run `summarize-mail-agentic.py` with the returned pipeline ID and workspace. Folder names and
IMAP UIDs are not part of this interface.

## Output

```text
~/.hermes/email/daily/YYYY-MM-DD/
├── emails/
│   └── <pipeline_id>/
│       ├── request.json
│       ├── message.eml
│       ├── manifest.json
│       ├── parts/
│       ├── attachments/
│       ├── links/
│       ├── summary.json
│       └── opencode-run/
│           ├── events.jsonl
│           └── metadata.json
├── scan-log/
│   └── scan-20260928T175900+0800.json
├── aggregation/
│   ├── aggregation-20260928T180435+0800.json
│   └── _run/
│       ├── events.jsonl
│       └── stderr.txt
```

Each scan log records only the scan run status, timestamps, mailbox failures,
counts, `rfc_message_ids`, and the derived pipeline ID partitions. Email
identity, metadata, IMAP location, and workspace information remain
authoritative in SQLite.

The stable identity is `SHA256(secret_salt || NUL || rfc_message_id)`. Folder
names and IMAP UIDs are stored only as mutable transport locations in
`~/.hermes/email/mail-index.sqlite3`; they are not used as identity or directory
names. Lookups:

```bash
mail-index.py --pipeline-id <pipeline_id>
mail-index.py --rfc-message-id '<message@example.com>'
```

The SQLite index keeps identity and transport location separate from a
one-to-one `email_metadata` record. Envelope scanning stores the available sent
time, subject, and sender. Fetching the RFC message then fills the complete
`From`, `To`, `Cc`, `Bcc`, `Reply-To`, `In-Reply-To`, and `References` headers.

Directories are created with mode `0700`; files are written atomically with
mode `0600`. Remote images and links are recorded but never fetched.

## Credentials

Do not put API keys in TOML. Store `DEEPSEEK_API_KEY` in `~/.hermes/.env`, the
Hermes vault, or an egress credential injector. Outlook OAuth token files stay
outside this repository.

## Deploy to Hermes

Run the test suite first, then install:

```bash
chmod +x install.sh
./install.sh
```

The installer backs up an existing pipeline as
`daily-mail-pipeline.py.bak-pre-mime-v2`, installs the parser package, and
compiles the deployed files. It does not alter OAuth tokens, TOML credentials,
cron jobs, or mailbox state.

For an existing deployment, migrate a literal model key and harden stored mail:

```bash
python3 tools/migrate_existing_config.py \
  --config ~/.hermes/scripts/email-pipeline/daily-mail-pipeline.toml \
  --env ~/.hermes/.env \
  --email-root ~/.hermes/email
```

The migration creates `daily-mail-pipeline.toml.bak-pre-mime-v2`, never prints
the secret, and is safe to run more than once.
