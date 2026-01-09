# Accounting Email Ingest Service

This service polls a mailbox over IMAP, extracts receipt attachments and message metadata, and forwards them to the accounting automation webhook. It runs as a systemd timer and moves processed messages to Archive folders for visibility and retry.

## What this does
- Connects to Dovecot IMAP on localhost (or configured host).
- Reads messages from the configured mailbox (default `INBOX`).
- Extracts PDF/image attachments and basic email metadata.
- POSTs a multipart or JSON payload to the accounting automation webhook.
- Moves successful messages to `Archive/Processed` and failures to `Archive/Failed`.
- Optionally retries failed messages each run.

## Prerequisites
- IMAP credentials for the accounting mailbox (username/password).
- Webhook URL and shared secret from the app:
  - `https://<our-domain>/api/v1/accounting/automation/ingest`
  - `ACCOUNTING_AUTOMATION_SECRET`

## Configuration
The service reads a simple env file:

`/etc/accounting-ingest/accounting-ingest.env`

Example (template is created on install):
```
WEBHOOK_URL=https://<our-domain>/api/v1/accounting/automation/ingest
ACCOUNTING_AUTOMATION_SECRET=
IMAP_HOST=127.0.0.1
IMAP_PORT=993
IMAP_TLS=true
IMAP_USERNAME=accounting@butteredupbakery.com
IMAP_PASSWORD=
IMAP_MAILBOX=INBOX
PROCESSED_MAILBOX=INBOX.Archive.Processed
FAILED_MAILBOX=INBOX.Archive.Failed
QUARANTINE_MAILBOX=INBOX.Archive.Quarantine
ALLOWED_MIME_TYPES=application/pdf,image/jpeg,image/png,image/heic
MAX_ATTACHMENT_BYTES=26214400
MAX_BODY_CHARS=20000
POLL_LIMIT=25
RETRY_FAILED=true
DRY_RUN=false
PROCESS_ALL=false
IMAP_TLS_VERIFY=false
```

Notes:
- `PROCESS_ALL=true` will process all messages in the mailbox instead of `UNSEEN`.
- `DRY_RUN=true` logs actions without webhook calls or IMAP moves.

## Install
From this repo:

```
./install/install.sh --apply
```

This will:
- Create `accounting-ingest` system user.
- Create `/opt/accounting-ingest` and copy files.
- Create `/etc/accounting-ingest/accounting-ingest.env` (if missing).
- Install systemd service/timer units.

Enable the timer:
```
sudo systemctl enable --now accounting-ingest.timer
```

## Run once (manual)
```
/usr/bin/python3 -m src.ingest --run-once
```

Process only failed mailbox:
```
/usr/bin/python3 -m src.ingest --retry-failed --mailbox Archive/Failed
```

Dry run (no webhook, no moves):
```
/usr/bin/python3 -m src.ingest --run-once --dry-run
```

## Troubleshooting
- View logs:
  - `journalctl -u accounting-ingest.service -f`
- IMAP auth errors:
  - Confirm `IMAP_USERNAME` and `IMAP_PASSWORD` are correct.
  - Confirm IMAP is enabled and listening on the configured port.
- Webhook 401/403:
  - Verify `ACCOUNTING_AUTOMATION_SECRET` is correct.
- Messages not moving:
  - Confirm mailbox paths (`Archive/Processed`, `Archive/Failed`) exist or can be created.
  - Check if the IMAP user has permission to create folders.

## Integration Contract (Webhook Payload)
This service calls the app webhook with the following contract.

### Endpoint
`POST https://<our-domain>/api/v1/accounting/automation/ingest`

### Headers
- `X-Accounting-Automation-Secret: <ACCOUNTING_AUTOMATION_SECRET>`
- `Origin: https://<our-domain>`
- `Referer: https://<our-domain>`
- `Content-Type: multipart/form-data` when attachments exist
- `Content-Type: application/json` when no attachments exist

### Payload (multipart/form-data)
Fields:
- `external_event_id`: Message-ID header (or `sha256:<hash>` fallback if missing)
- `from_address`
- `to_address`
- `subject`
- `body_text`
- `body_html`
- `metadata`: JSON string
- `files`: 1..n attachments (field name is `files`)

### Payload (application/json, no attachments)
Same fields as above, except no `files`.

### Metadata object (JSON)
- `source`: "email"
- `received_date`: parsed Date header if present
- `message_id`: Message-ID header
- `return_path`
- `reply_to`
- `cc`
- `headers_subset`: subset of headers (message_id, subject, from, to, date)
- `attachments_count`: number of attachments sent
- `attachments_skipped`: list of skipped attachments (filename, content_type, size, reason)
- `attachments_skipped_count`

### Attachment rules
- Allowed MIME types: `application/pdf`, `image/jpeg`, `image/png`, `image/heic`
- Enforces `MAX_ATTACHMENT_BYTES` (default 25MB)
- Inline parts are ignored unless they have a filename or `Content-Disposition: attachment`
- If no allowed attachments remain, the service sends JSON-only with metadata and body

### Idempotency
- `external_event_id` is the email Message-ID when available
- If Message-ID is missing, the service uses `sha256:<hash>` of the raw message
- The app dedupes by `external_event_id` and attachment hashes

### Example (multipart)
```
curl -X POST "https://<our-domain>/api/v1/accounting/automation/ingest" \
  -H "X-Accounting-Automation-Secret: $ACCOUNTING_AUTOMATION_SECRET" \
  -F "external_event_id=<message-id>" \
  -F "from_address=sender@example.com" \
  -F "to_address=accounting@butteredupbakery.com" \
  -F "subject=Receipt" \
  -F "body_text=Thanks for your purchase" \
  -F "metadata={\"source\":\"email\"}" \
  -F "files=@/path/to/receipt.pdf;type=application/pdf"
```

### Example (JSON-only)
```
curl -X POST "https://<our-domain>/api/v1/accounting/automation/ingest" \
  -H "X-Accounting-Automation-Secret: $ACCOUNTING_AUTOMATION_SECRET" \
  -H "Content-Type: application/json" \
  -d '{
    "external_event_id": "<message-id>",
    "from_address": "sender@example.com",
    "to_address": "accounting@butteredupbakery.com",
    "subject": "No attachment",
    "body_text": "See note in body",
    "metadata": {"source": "email"}
  }'
```

### Webhook behavior
- On 2xx response: message moves to `Archive/Processed`
- On error: message moves to `Archive/Failed`
- If `DRY_RUN=true`, no webhook calls are made

## Uninstall
```
./install/uninstall.sh --apply
```

This disables the timer and removes `/opt/accounting-ingest`. It leaves the env file in `/etc/accounting-ingest` for safety.
