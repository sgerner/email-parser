import unittest
from types import SimpleNamespace

from src.postfix_pipe import (
	UnknownRecipientError,
	_infer_target_from_recipient,
	_resolve_message_route,
)


class _FakeResolver:
	def __init__(self, result):
		self.result = result

	def resolve(self, recipient_email):
		return self.result


class PostfixPipeTests(unittest.TestCase):
	def test_infer_target_from_recipient_suffix(self):
		self.assertEqual(_infer_target_from_recipient("buttered-up-po@farin.app"), "po")
		self.assertEqual(_infer_target_from_recipient("buttered-up-accounting@farin.app"), "accounting")

	def test_infer_target_from_recipient_direct_local_part(self):
		self.assertEqual(_infer_target_from_recipient("po@butteredupbakery.com"), "po")
		self.assertEqual(_infer_target_from_recipient("accounting@butteredupbakery.com"), "accounting")

	def test_resolve_message_route_dynamic_uses_resolver(self):
		config = SimpleNamespace(
			use_supabase_routes=True,
			recipient_header_priority=["x-original-to", "to"],
		)
		parsed = SimpleNamespace(
			metadata={
				"recipient_header_candidates": {
					"x-original-to": ["buttered-up-po@farin.app"]
				},
				"headers_subset": {"to": "ignored@example.com"},
			}
		)
		resolved = SimpleNamespace(
			channel="po",
			company_id="company-123",
			email_address="buttered-up-po@farin.app",
		)

		target, company_id, recipient = _resolve_message_route(
			config,
			parsed,
			recipient_override=None,
			route_resolver=_FakeResolver(result=resolved),
		)

		self.assertEqual(target, "po")
		self.assertEqual(company_id, "company-123")
		self.assertEqual(recipient, "buttered-up-po@farin.app")

	def test_resolve_message_route_dynamic_unknown_raises(self):
		config = SimpleNamespace(
			use_supabase_routes=True,
			recipient_header_priority=["x-original-to", "to"],
		)
		parsed = SimpleNamespace(
			metadata={
				"recipient_header_candidates": {
					"x-original-to": ["missing@farin.app"]
				},
				"headers_subset": {"to": "ignored@example.com"},
			}
		)

		with self.assertRaises(UnknownRecipientError):
			_resolve_message_route(
				config,
				parsed,
				recipient_override=None,
				route_resolver=_FakeResolver(result=None),
			)


if __name__ == "__main__":
	unittest.main()
