import unittest

from src.config import load_config


class ConfigTests(unittest.TestCase):
    def test_legacy_accounting_webhook_and_imap_fallback_remain_supported(self):
        config = load_config(
            env_path="/tmp/does-not-exist.env",
            overrides={
                "WEBHOOK_URL": "https://example.com/api/v1/accounting/automation/ingest",
                "ACCOUNTING_AUTOMATION_SECRET": "secret",
                "IMAP_USERNAME": "accounting@butteredupbakery.com",
                "IMAP_PASSWORD": "pass",
                "IMAP_MAILBOX": "INBOX",
            },
        )

        self.assertEqual(config.accounting_webhook_url, "https://example.com/api/v1/accounting/automation/ingest")
        self.assertEqual(config.po_webhook_url, "")
        self.assertEqual(config.accounting_imap_username, "accounting@butteredupbakery.com")
        self.assertEqual(config.accounting_imap_password, "pass")
        self.assertEqual(config.accounting_imap_mailbox, "INBOX")

    def test_explicit_route_config_is_loaded(self):
        config = load_config(
            env_path="/tmp/does-not-exist.env",
            overrides={
                "ACCOUNTING_WEBHOOK_URL": "https://example.com/api/v1/accounting/automation/ingest",
                "PO_WEBHOOK_URL": "https://example.com/api/v1/customers/wholesale/orders/po-drafts",
                "ACCOUNTING_IMAP_USERNAME": "accounting@butteredupbakery.com",
                "ACCOUNTING_IMAP_PASSWORD": "acc-pass",
                "PO_IMAP_USERNAME": "po@butteredupbakery.com",
                "PO_IMAP_PASSWORD": "po-pass",
            },
        )

        self.assertEqual(config.accounting_webhook_url, "https://example.com/api/v1/accounting/automation/ingest")
        self.assertEqual(config.po_webhook_url, "https://example.com/api/v1/customers/wholesale/orders/po-drafts")
        self.assertEqual(config.accounting_imap_username, "accounting@butteredupbakery.com")
        self.assertEqual(config.po_imap_username, "po@butteredupbakery.com")


if __name__ == "__main__":
    unittest.main()
