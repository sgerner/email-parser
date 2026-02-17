# Email Parser (Strict Mailbox Routing)

This service monitors mailbox routes and forwards each route to exactly one webhook:

- `accounting@butteredupbakery.com` inbox -> Accounting automation webhook (`/api/v1/accounting/automation/ingest`)
- `po@butteredupbakery.com` inbox -> PO draft webhook (`/api/v1/customers/wholesale/orders/po-drafts`)

No cross-posting is performed.

## Required auth header
Both webhook requests include:
- `X-Accounting-Automation-Secret: <ACCOUNTING_AUTOMATION_SECRET>`
- `X-Company-Id: <COMPANY_ID>` (if configured)

## Routing behavior
- Accounting route calls only `ACCOUNTING_WEBHOOK_URL`.
- PO route calls only `PO_WEBHOOK_URL`.
- `2xx` response moves message to `Archive/Processed`.
- Non-`2xx` or request error moves message to `Archive/Failed`.
- `DRY_RUN=true` makes no webhook calls and no mailbox moves.

## Configuration

Env file: `/etc/accounting-ingest/accounting-ingest.env`

Example:
```
COMPANY_ID=your-company-id
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
- `COMPANY_ID` is sent as `X-Company-Id` header and injected into payloads.
- If `ACCOUNTING_IMAP_*` is unset, accounting route falls back to legacy `IMAP_*`.
- Legacy `WEBHOOK_URL` is still recognized if it points to either known endpoint.

## Payload behavior by route

### Accounting route
- Target: `ACCOUNTING_WEBHOOK_URL`
- Uses accounting payload shape:
  - `company_id` (if configured)
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
  - `metadata` (includes `company_id` if configured)
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
3. Configure `COMPANY_ID`.
4. Send a test email to accounting inbox; verify only accounting webhook receives it and includes `X-Company-Id`.
5. Send a test email to PO inbox; verify only PO webhook receives it and includes `X-Company-Id`.
6. For PO, send multiple attachments and confirm only one `file` is posted by priority.
7. Confirm `X-Accounting-Automation-Secret` header on both integrations.
8. Confirm Processed/Failed mailbox moves for success and failure.
9. Confirm dry-run prevents calls and moves.

## Tests
```bash
python3 -m unittest discover -s tests -v
```
