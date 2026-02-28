# Email Parser (IMAP Ingest + Webhook Routing)

This service reads email from IMAP mailboxes and forwards each message to one webhook route:

- `po` -> `/api/v1/customers/wholesale/orders/po-drafts`
- `accounting` -> `/api/v1/accounting/automation/ingest`

It supports two routing modes:

1. Dynamic multi-tenant routing (recommended): resolve recipient alias in Supabase (`*-po@farin.app`, `*-accounting@farin.app`).
2. Legacy static routing: fixed mailbox credentials and optional fixed `COMPANY_ID`.

## Required auth header
Both webhook requests include:
- `X-Accounting-Automation-Secret: <ACCOUNTING_AUTOMATION_SECRET>`
- `X-Company-Id: <resolved company id>` when available

## Dynamic Routing (recommended)

Set:
```bash
USE_SUPABASE_ROUTES=true
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<service-role-key>
SUPABASE_TIMEOUT_SECONDS=10
RECIPIENT_HEADER_PRIORITY=x-original-to,delivered-to,envelope-to,x-envelope-to,to
```

Mailbox for dynamic mode (only if you keep IMAP polling):
```bash
IMAP_USERNAME=<single-inbound-mailbox>
IMAP_PASSWORD=<password>
IMAP_MAILBOX=INBOX
```

The service extracts recipient using `RECIPIENT_HEADER_PRIORITY`, resolves route through Supabase RPC `resolve_company_inbound_email_route`, then:
- sets channel (`po` or `accounting`)
- sets per-message `company_id`
- posts to the matching webhook

If route lookup fails, message moves to `QUARANTINE_MAILBOX` (or `FAILED_MAILBOX` if quarantine is unset).

## Postfix Pipe (most efficient)

Use this to avoid IMAP polling and process messages immediately at SMTP delivery time.

`install/install.sh --apply` now configures this automatically:
- Creates `/etc/postfix/transport_farin.regexp`
- Appends `farin_ingest` transport to `/etc/postfix/master.cf` (idempotent)
- Updates `transport_maps` and `farin_ingest_destination_recipient_limit` via `postconf -e`
- Runs `postfix check` and reloads postfix

Manual reference (if you need to apply by hand):

1. Add a Postfix transport in `/etc/postfix/master.cf`:
```conf
farin_ingest unix  -       n       n       -       -       pipe
  flags=Rq user=accounting-ingest argv=/usr/bin/python3 /opt/accounting-ingest/src/postfix_pipe.py --config /etc/accounting-ingest/accounting-ingest.env --recipient ${original_recipient} --unknown-recipient-action bounce
```

2. Add a transport map in `/etc/postfix/transport`:
```conf
/^[a-z0-9-]+-(po|accounting)@farin\.app$/ farin_ingest:
```

3. Enable map and single-recipient delivery in `/etc/postfix/main.cf`:
```conf
transport_maps = regexp:/etc/postfix/transport
farin_ingest_destination_recipient_limit = 1
```

4. Reload Postfix:
```bash
sudo postfix reload
```

5. Optional: disable IMAP polling timer if fully migrated:
```bash
sudo systemctl disable --now accounting-ingest.timer
```

## Legacy Static Routing

Set:
```bash
USE_SUPABASE_ROUTES=false
COMPANY_ID=<fixed-company-id-optional>
ACCOUNTING_IMAP_USERNAME=accounting@...
ACCOUNTING_IMAP_PASSWORD=...
ACCOUNTING_IMAP_MAILBOX=INBOX
PO_IMAP_USERNAME=po@...
PO_IMAP_PASSWORD=...
PO_IMAP_MAILBOX=INBOX
```

## Configuration

Env file: `/etc/accounting-ingest/accounting-ingest.env`

Core webhook/env options:
```bash
PO_WEBHOOK_URL=https://<our-domain>/api/v1/customers/wholesale/orders/po-drafts
ACCOUNTING_WEBHOOK_URL=https://<our-domain>/api/v1/accounting/automation/ingest
ACCOUNTING_AUTOMATION_SECRET=

IMAP_HOST=127.0.0.1
IMAP_PORT=993
IMAP_TLS=true
IMAP_TLS_VERIFY=false

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
```

## Payload behavior by route

### Accounting route
- Target: `ACCOUNTING_WEBHOOK_URL`
- Payload:
  - `company_id` (resolved dynamically or fixed)
  - `external_event_id`
  - `from_address`, `to_address`, `subject`
  - `body_text`, `body_html`
  - `metadata`
  - multipart `files` for allowed attachments

### PO route
- Target: `PO_WEBHOOK_URL`
- Payload:
  - `source_ref`, `source_type=email`
  - `from_address`, `to_address`, `subject`
  - `body_text`, `body_html`
  - `metadata`
  - multipart single `file` when attachment exists

PO attachment selection priority:
1. `application/pdf`
2. image (`image/jpeg`, `image/png`, `image/webp`, `image/heic`, `image/heif`)
3. doc/docx (`application/msword`, `application/vnd.openxmlformats-officedocument.wordprocessingml.document`)
4. text/csv (`text/plain`, `text/csv`, `application/csv`)

## Message lifecycle
- `2xx` -> move to `Archive/Processed`
- non-`2xx`/error -> move to `Archive/Failed`
- unresolved route -> move to `Archive/Quarantine` (or Failed if quarantine unset)
- `DRY_RUN=true` -> no webhook calls and no IMAP moves

## Install
```bash
./install/install.sh --apply
```

Optional installer flags:
```bash
# Force smoke test at end of install
./install/install.sh --apply --smoke-test

# Force smoke test with specific recipient
./install/install.sh --apply --smoke-test --smoke-test-recipient buttered-up-po@farin.app

# Skip smoke test prompt
./install/install.sh --apply --no-smoke-test
```

If still using IMAP polling mode:
```bash
sudo systemctl enable --now accounting-ingest.timer
```

## Run once
```bash
/usr/bin/python3 -m src.ingest --run-once
```

## Tests
```bash
python3 -m unittest discover -s tests -v
```
