# Email Parser (Strict Mailbox Routing)

This service monitors mailbox routes and forwards each route to exactly one webhook:

- `accounting@butteredupbakery.com` inbox -> Accounting automation webhook (`/api/v1/accounting/automation/ingest`)
- `po@butteredupbakery.com` inbox -> PO draft webhook (`/api/v1/customers/wholesale/orders/po-drafts`)

No cross-posting is performed.

## Required auth header
Both webhook requests include:
- `X-Accounting-Automation-Secret: <ACCOUNTING_AUTOMATION_SECRET>`

## Routing behavior
- Accounting route calls only `ACCOUNTING_WEBHOOK_URL`.
- PO route calls only `PO_WEBHOOK_URL`.
- `2xx` response moves message to `Archive/Processed`.
- Non-`2xx` or request error moves message to `Archive/Failed`.
- `DRY_RUN=true` makes no webhook calls and no mailbox moves.

## Config summary
Use explicit per-route credentials:
- `ACCOUNTING_IMAP_USERNAME`, `ACCOUNTING_IMAP_PASSWORD`, `ACCOUNTING_IMAP_MAILBOX`
- `PO_IMAP_USERNAME`, `PO_IMAP_PASSWORD`, `PO_IMAP_MAILBOX`
- `ACCOUNTING_WEBHOOK_URL`
- `PO_WEBHOOK_URL`
- `ACCOUNTING_AUTOMATION_SECRET`

Backward compatibility:
- If `ACCOUNTING_IMAP_*` is not set, accounting route falls back to legacy `IMAP_*` values.
- Legacy `WEBHOOK_URL` is still accepted when it points to one known endpoint.

Full setup and payload contract:
- `/home/steven/code/email-parser/docs/README.md`

## Tests
```bash
python3 -m unittest discover -s tests -v
```
