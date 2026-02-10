import unittest
import imaplib
from types import SimpleNamespace
from unittest.mock import Mock, patch

import src.ingest as ingest
from src.ingest import (
    MailRoute,
    build_routes,
    select_attachment_for_webhook,
    send_accounting_message,
    send_po_message,
)


class IngestTests(unittest.TestCase):
    def test_attachment_priority_prefers_pdf_then_tracks_single_file_skips(self):
        attachments = [
            {"filename": "notes.txt", "content_type": "text/plain", "payload": b"a", "size": 1},
            {"filename": "photo.png", "content_type": "image/png", "payload": b"b", "size": 1},
            {
                "filename": "po.docx",
                "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "payload": b"c",
                "size": 1,
            },
            {"filename": "po.pdf", "content_type": "application/pdf", "payload": b"d", "size": 1},
        ]
        allowed = [
            "application/pdf",
            "image/jpeg",
            "image/png",
            "image/webp",
            "image/heic",
            "image/heif",
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "text/plain",
            "text/csv",
            "application/csv",
        ]

        selected, skipped = select_attachment_for_webhook(attachments, allowed, max_bytes=1000)

        self.assertIsNotNone(selected)
        self.assertEqual(selected["filename"], "po.pdf")
        self.assertEqual(len(skipped), 3)
        self.assertTrue(all(item["reason"] == "single_file_only" for item in skipped))

    def test_attachment_skip_reasons_include_too_large_and_unsupported(self):
        attachments = [
            {"filename": "big.pdf", "content_type": "application/pdf", "payload": b"x" * 11, "size": 11},
            {"filename": "run.exe", "content_type": "application/octet-stream", "payload": b"a", "size": 1},
            {"filename": "small.txt", "content_type": "text/plain", "payload": b"ok", "size": 2},
        ]
        allowed = ["application/pdf", "text/plain"]

        selected, skipped = select_attachment_for_webhook(attachments, allowed, max_bytes=10)

        self.assertIsNotNone(selected)
        self.assertEqual(selected["filename"], "small.txt")
        reasons = {item["filename"]: item["reason"] for item in skipped}
        self.assertEqual(reasons.get("big.pdf"), "too_large")
        self.assertEqual(reasons.get("run.exe"), "unsupported_type")

    def test_send_po_message_only_uses_po_webhook(self):
        config = SimpleNamespace(
            po_webhook_url="https://example.com/api/v1/customers/wholesale/orders/po-drafts",
            accounting_webhook_url="https://example.com/api/v1/accounting/automation/ingest",
        )
        parsed = SimpleNamespace(
            body_text="hello",
            body_html=None,
            attachments=[{"filename": "po.pdf", "content_type": "application/pdf", "size": 3}],
            metadata={
                "message_id": "<msg@example.com>",
                "headers_subset": {
                    "from": "sender@example.com",
                    "to": "po@butteredupbakery.com",
                    "subject": "PO",
                },
            },
        )
        po_attachment = {"filename": "po.pdf", "content_type": "application/pdf", "payload": b"pdf", "size": 3}

        with patch("src.ingest.post_po_webhook", return_value=(201, "{}")) as po_mock, patch(
            "src.ingest.post_accounting_webhook", return_value=(201, "{}")
        ) as accounting_mock:
            ok = send_po_message(
                config=config,
                parsed=parsed,
                raw_bytes=b"raw-email",
                po_attachment=po_attachment,
                skipped=[],
                dry_run=False,
            )

        self.assertTrue(ok)
        self.assertEqual(po_mock.call_count, 1)
        self.assertEqual(accounting_mock.call_count, 0)

    def test_send_accounting_message_only_uses_accounting_webhook(self):
        config = SimpleNamespace(
            po_webhook_url="https://example.com/api/v1/customers/wholesale/orders/po-drafts",
            accounting_webhook_url="https://example.com/api/v1/accounting/automation/ingest",
        )
        parsed = SimpleNamespace(
            body_text="hello",
            body_html=None,
            attachments=[{"filename": "receipt.pdf", "content_type": "application/pdf", "size": 3}],
            metadata={
                "message_id": "<msg@example.com>",
                "headers_subset": {
                    "from": "sender@example.com",
                    "to": "accounting@butteredupbakery.com",
                    "subject": "Receipt",
                },
            },
        )
        attachments = [{"filename": "receipt.pdf", "content_type": "application/pdf", "payload": b"pdf", "size": 3}]

        with patch("src.ingest.post_accounting_webhook", return_value=(200, "{}")) as accounting_mock, patch(
            "src.ingest.post_po_webhook", return_value=(201, "{}")
        ) as po_mock:
            ok = send_accounting_message(
                config=config,
                parsed=parsed,
                raw_bytes=b"raw-email",
                attachments=attachments,
                skipped=[],
                dry_run=False,
            )

        self.assertTrue(ok)
        self.assertEqual(accounting_mock.call_count, 1)
        self.assertEqual(po_mock.call_count, 0)

    def test_build_routes_creates_separate_mailbox_routes(self):
        config = SimpleNamespace(
            accounting_webhook_url="https://example.com/api/v1/accounting/automation/ingest",
            po_webhook_url="https://example.com/api/v1/customers/wholesale/orders/po-drafts",
            accounting_imap_username="accounting@butteredupbakery.com",
            accounting_imap_password="acc-pass",
            accounting_imap_mailbox="INBOX",
            po_imap_username="po@butteredupbakery.com",
            po_imap_password="po-pass",
            po_imap_mailbox="INBOX",
            accounting_allowed_mime_types=["application/pdf"],
            po_allowed_mime_types=["application/pdf"],
        )

        routes = build_routes(config)

        self.assertEqual(len(routes), 2)
        self.assertEqual(routes[0].target, "accounting")
        self.assertEqual(routes[0].imap_username, "accounting@butteredupbakery.com")
        self.assertEqual(routes[1].target, "po")
        self.assertEqual(routes[1].imap_username, "po@butteredupbakery.com")

    def test_main_continues_other_routes_after_auth_failure_and_exits_nonzero(self):
        parser = Mock()
        parser.parse_args.return_value = SimpleNamespace(
            run_once=True,
            mailbox=None,
            limit=None,
            retry_failed=False,
            dry_run=False,
            keep_in_inbox=False,
            process_all=False,
            config=None,
        )
        config = SimpleNamespace(
            accounting_automation_secret="secret",
            accounting_webhook_url="https://example.com/api/v1/accounting/automation/ingest",
            po_webhook_url="https://example.com/api/v1/customers/wholesale/orders/po-drafts",
            poll_limit=25,
            retry_failed=False,
            dry_run=False,
            process_all=False,
            processed_mailbox="INBOX.Archive.Processed",
            failed_mailbox="INBOX.Archive.Failed",
            quarantine_mailbox=None,
        )
        routes = [
            MailRoute(
                name="accounting",
                target="accounting",
                imap_username="accounting@butteredupbakery.com",
                imap_password="bad-pass",
                imap_mailbox="INBOX",
                allowed_mime_types=["application/pdf"],
            ),
            MailRoute(
                name="po",
                target="po",
                imap_username="po@butteredupbakery.com",
                imap_password="good-pass",
                imap_mailbox="INBOX",
                allowed_mime_types=["application/pdf"],
            ),
        ]
        po_client = Mock()
        with patch("src.ingest.setup_logging"), patch("src.ingest.build_parser", return_value=parser), patch(
            "src.ingest.load_config", return_value=config
        ), patch("src.ingest.build_routes", return_value=routes), patch(
            "src.ingest.connect",
            side_effect=[imaplib.IMAP4.error(b"[AUTHENTICATIONFAILED] Authentication failed."), po_client],
        ) as connect_mock, patch("src.ingest.ensure_mailbox"), patch("src.ingest.process_mailbox") as process_mock:
            with self.assertRaises(SystemExit) as err:
                ingest.main()
        self.assertEqual(err.exception.code, 1)
        self.assertEqual(connect_mock.call_count, 2)
        process_mock.assert_called_once()
        po_client.logout.assert_called_once()


if __name__ == "__main__":
    unittest.main()
