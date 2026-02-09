# Email Ingest Service

This service polls IMAP mailboxes and routes emails by mailbox to the correct webhook:

- Accounting mailbox route -> accounting automation webhook
- PO mailbox route -> wholesale PO draft webhook

It preserves Processed/Failed archive behavior and dry-run behavior.

## Strict routing
- `accounting@butteredupbakery.com` mailbox messages are sent only to accounting webhook.
- `po@butteredupbakery.com` mailbox messages are sent only to PO webhook.
- No cross-posting between integrations.

## Endpoints
- Accounting: `POST https://<our-domain>/api/v1/accounting/automation/ingest`
- PO drafts: `POST https://<our-domain>/api/v1/customers/wholesale/orders/po-drafts`
- Auth header on both:
  - `X-Accounting-Automation-Secret: <ACCOUNTING_AUTOMATION_SECRET>`

## Configuration
Env file:

`/etc/accounting-ingest/accounting-ingest.env`

Example:
```
ACCOUNTING_WEBHOOK_URL=https://<our-domain>/api/v1/accounting/automation/ingest
PO_WEBHOOK_URL=https://<our-domain>/api/v1/customers/wholesale/orders/po-drafts
ACCOUNTING_AUTOMATION_SECRET=

IMAP_HOST=127.0.0.1
IMAP_PORT=993
IMAP_TLS=true
IMAP_TLS_VERIFY=false

ACCOUNTING_IMAP_USERNAME=accounting@butteredupbakery.com
ACCOUNTING_IMAP_PASSWORD=
ACCOUNTING_IMAP_MAILBOX=INBOX

PO_IMAP_USERNAME=po@butteredupbakery.com
PO_IMAP_PASSWORD=
PO_IMAP_MAILBOX=INBOX

PROCESSED_MAILBOX=INBOX.Archive.Processed
FAILED_MAILBOX=INBOX.Archive.Failed
QUARANTINE_MAILBOX=INBOX.Archive.Quarantine

ACCOUNTING_ALLOWED_MIME_TYPES=application/pdf,image/jpeg,image/png,image/heic,text/csv,application/csv
PO_ALLOWED_MIME_TYPES=application/pdf,image/jpeg,image/png,image/webp,image/heic,image/heif,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain,text/csv,application/csv

MAX_ATTACHMENT_BYTES=26214400
MAX_BODY_CHARS=20000
POLL_LIMIT=25
RETRY_FAILED=true
DRY_RUN=false
PROCESS_ALL=false

# Legacy fallback (optional)
IMAP_USERNAME=
IMAP_PASSWORD=
IMAP_MAILBOX=INBOX
```

Compatibility:
- If `ACCOUNTING_IMAP_*` is unset, accounting route falls back to legacy `IMAP_*`.
- Legacy `WEBHOOK_URL` is still recognized if it points to either known endpoint.

## Payload behavior by route

### Accounting route
- Target: `ACCOUNTING_WEBHOOK_URL`
- Uses accounting payload shape:
  - `external_event_id`
  - `from_address`, `to_address`, `subject`
  - `body_text`, `body_html`
  - `metadata`
  - multipart `files` for allowed attachments
  - JSON-only when no allowed attachment

### PO route
- Target: `PO_WEBHOOK_URL`
- Uses PO draft payload shape:
  - `source_ref`, `source_type=email`
  - `from_address`, `to_address`, `subject`
  - `body_text`, `body_html`
  - `metadata`
  - multipart single `file` when attachment exists
  - JSON-only when no valid attachment

PO attachment selection priority:
- `application/pdf`
- image (`image/jpeg`, `image/png`, `image/webp`, `image/heic`, `image/heif`)
- doc/docx (`application/msword`, `application/vnd.openxmlformats-officedocument.wordprocessingml.document`)
- text/csv (`text/plain`, `text/csv`, `application/csv`)

Skipped items include reasons (`too_large`, `unsupported_type`, `single_file_only`) in metadata.

## Message lifecycle
- `2xx` -> move to `Archive/Processed`
- non-`2xx`/error -> move to `Archive/Failed`
- `DRY_RUN=true` -> no webhook calls and no IMAP moves

## Install
```
./install/install.sh --apply
sudo systemctl enable --now accounting-ingest.timer
```

## Run once
```
/usr/bin/python3 -m src.ingest --run-once
```

## Manual validation checklist
1. Configure `ACCOUNTING_IMAP_*` + `ACCOUNTING_WEBHOOK_URL`.
2. Configure `PO_IMAP_*` + `PO_WEBHOOK_URL`.
3. Send a test email to accounting inbox; verify only accounting webhook receives it.
4. Send a test email to PO inbox; verify only PO webhook receives it.
5. For PO, send multiple attachments and confirm only one `file` is posted by priority.
6. Confirm `X-Accounting-Automation-Secret` header on both integrations.
7. Confirm Processed/Failed mailbox moves for success and failure.
8. Confirm dry-run prevents calls and moves.

## Ops migration note
- Existing accounting automation can remain unchanged:
  - keep accounting webhook URL and IMAP credentials.
  - if you already use `IMAP_*`, it still works for accounting route.
- To add PO ingest, configure `PO_WEBHOOK_URL` and `PO_IMAP_*`.
