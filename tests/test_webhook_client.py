import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.webhook_client import post_webhook, post_accounting_webhook


class _FakeResponse:
    def __init__(self, status=200, body=b"{}"):
        self.status = status
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class WebhookClientTests(unittest.TestCase):
    def setUp(self):
        self.config = SimpleNamespace(
            po_webhook_url="https://example.com/api/v1/customers/wholesale/orders/po-drafts",
            accounting_webhook_url="https://example.com/api/v1/accounting/automation/ingest",
            accounting_automation_secret="secret-token",
        )

    def test_post_webhook_multipart_sends_single_file_and_metadata(self):
        payload = {
            "from_address": "sender@example.com",
            "to_address": "po@butteredupbakery.com",
            "subject": "PO-123",
            "body_text": "See attached",
            "body_html": "<p>See attached</p>",
            "source_ref": "<msg@example.com>",
            "source_type": "email",
            "metadata": {"source": "email", "attachments_count": 1},
        }
        attachment = {
            "filename": "po.pdf",
            "content_type": "application/pdf",
            "payload": b"PDF-BYTES",
        }
        captured = {}

        def fake_urlopen(req, timeout):
            captured["headers"] = {key.lower(): value for key, value in req.header_items()}
            captured["body"] = req.data
            captured["timeout"] = timeout
            return _FakeResponse(status=201, body=b'{"ok":true}')

        with patch("src.webhook_client.request.urlopen", side_effect=fake_urlopen):
            status, body = post_webhook(self.config, payload, attachment, timeout=17)

        self.assertEqual(status, 201)
        self.assertEqual(body, '{"ok":true}')
        self.assertEqual(captured["headers"].get("x-accounting-automation-secret"), "secret-token")
        self.assertIn("multipart/form-data", captured["headers"].get("content-type", ""))
        self.assertEqual(captured["timeout"], 17)

        body_bytes = captured["body"]
        self.assertIn(b'name="file"; filename="po.pdf"', body_bytes)
        self.assertNotIn(b'name="files"', body_bytes)
        self.assertIn(b'name="source_ref"', body_bytes)
        self.assertIn(b'<msg@example.com>', body_bytes)
        self.assertIn(b'name="source_type"', body_bytes)
        self.assertIn(b'email', body_bytes)
        self.assertIn(b'name="metadata"', body_bytes)
        self.assertIn(b'"attachments_count": 1', body_bytes)

    def test_post_webhook_json_when_no_attachment(self):
        payload = {
            "from_address": "sender@example.com",
            "to_address": "po@butteredupbakery.com",
            "subject": "Body-only PO",
            "body_text": "please draft from this email",
            "body_html": None,
            "source_ref": "sha256:abcd",
            "source_type": "email",
            "metadata": {"source": "email", "attachments_count": 0},
        }
        captured = {}

        def fake_urlopen(req, timeout):
            captured["headers"] = {key.lower(): value for key, value in req.header_items()}
            captured["body"] = req.data
            return _FakeResponse(status=200, body=b"{}")

        with patch("src.webhook_client.request.urlopen", side_effect=fake_urlopen):
            status, _ = post_webhook(self.config, payload, None)

        self.assertEqual(status, 200)
        self.assertEqual(captured["headers"].get("x-accounting-automation-secret"), "secret-token")
        self.assertEqual(captured["headers"].get("content-type"), "application/json")

        decoded = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual(decoded["source_ref"], "sha256:abcd")
        self.assertEqual(decoded["source_type"], "email")
        self.assertEqual(decoded["body_text"], "please draft from this email")

    def test_post_accounting_webhook_multipart_uses_files_and_external_event_id(self):
        payload = {
            "external_event_id": "<msg@example.com>",
            "from_address": "sender@example.com",
            "to_address": "accounting@butteredupbakery.com",
            "subject": "Receipt",
            "body_text": "see attached",
            "body_html": None,
            "metadata": {"source": "email"},
        }
        attachments = [
            {
                "filename": "receipt.pdf",
                "content_type": "application/pdf",
                "payload": b"PDF-BYTES",
            },
            {
                "filename": "receipt.csv",
                "content_type": "text/csv",
                "payload": b"a,b,c",
            },
        ]
        captured = {}

        def fake_urlopen(req, timeout):
            captured["headers"] = {key.lower(): value for key, value in req.header_items()}
            captured["body"] = req.data
            return _FakeResponse(status=200, body=b"{}")

        with patch("src.webhook_client.request.urlopen", side_effect=fake_urlopen):
            status, _ = post_accounting_webhook(self.config, payload, attachments)

        self.assertEqual(status, 200)
        self.assertEqual(captured["headers"].get("x-accounting-automation-secret"), "secret-token")
        self.assertIn("multipart/form-data", captured["headers"].get("content-type", ""))
        self.assertIn(b'name="files"; filename="receipt.pdf"', captured["body"])
        self.assertIn(b'name="files"; filename="receipt.csv"', captured["body"])
        self.assertIn(b'name="external_event_id"', captured["body"])
        self.assertIn(b'<msg@example.com>', captured["body"])


if __name__ == "__main__":
    unittest.main()
