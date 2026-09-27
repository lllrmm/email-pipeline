# Email Pipeline

Read-only Outlook email extraction and digest pipeline for Hermes Agent.

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
daily-mail-pipeline.py             Production pipeline entry point
src/email_pipeline/mime_extract.py MIME and attachment extraction library
daily-mail-pipeline.yaml.example   Configuration without credentials
tests/                             Synthetic MIME regression tests
```

## Test

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m py_compile daily-mail-pipeline.py src/email_pipeline/*.py
```

The test suite never connects to a mailbox or model API.

For a read-only shadow run on a real mailbox without model calls:

```bash
python3 daily-mail-pipeline.py \
  --date 2026-09-27 \
  --no-summary \
  --output-root ~/.hermes/email/daily-v2-shadow
```

To replay one known message without processing the rest of the day:

```bash
python3 daily-mail-pipeline.py \
  --date 2026-09-25 \
  --mailbox Inbox \
  --message-id 27329 \
  --output-root ~/.hermes/email/single-message-check
```

## Output

```text
~/.hermes/email/daily/YYYY-MM-DD/
├── bundle.json
├── eml/
├── raw/
└── attachments/
    └── Mailbox__MessageId/
        ├── manifest.json
        ├── <sha256-prefix>.<ext>
        └── <sha256-prefix>.<ext>.txt
```

Directories are created with mode `0700`; files are written atomically with
mode `0600`. Remote images and links are recorded but never fetched.

## Credentials

Do not put API keys in YAML. Store `DEEPSEEK_API_KEY` in `~/.hermes/.env`, the
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
compiles the deployed files. It does not alter OAuth tokens, YAML credentials,
cron jobs, or mailbox state.
