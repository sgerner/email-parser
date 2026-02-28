import json
import unittest
from unittest.mock import patch

from src.route_resolver import RouteResolverError, SupabaseRouteResolver


class _FakeResponse:
	def __init__(self, status=200, body=b"[]"):
		self.status = status
		self._body = body

	def read(self):
		return self._body

	def __enter__(self):
		return self

	def __exit__(self, exc_type, exc, tb):
		return False


class RouteResolverTests(unittest.TestCase):
	def test_resolve_returns_route_and_uses_cache(self):
		resolver = SupabaseRouteResolver(
			supabase_url="https://example.supabase.co",
			service_role_key="service-role-key",
			timeout_seconds=5,
		)
		call_count = {"value": 0}

		def fake_urlopen(req, timeout):
			call_count["value"] += 1
			self.assertEqual(timeout, 5)
			body = json.dumps(
				[
					{
						"company_id": "company-1",
						"channel": "po",
						"email_address": "buttered-up-po@farin.app",
					}
				]
			).encode("utf-8")
			return _FakeResponse(status=200, body=body)

		with patch("src.route_resolver.request.urlopen", side_effect=fake_urlopen):
			first = resolver.resolve("BUTTERED-UP-PO@FARIN.APP")
			second = resolver.resolve("buttered-up-po@farin.app")

		self.assertIsNotNone(first)
		self.assertEqual(first.company_id, "company-1")
		self.assertEqual(first.channel, "po")
		self.assertEqual(first.email_address, "buttered-up-po@farin.app")
		self.assertIs(first, second)
		self.assertEqual(call_count["value"], 1)

	def test_resolve_invalid_payload_raises(self):
		resolver = SupabaseRouteResolver(
			supabase_url="https://example.supabase.co",
			service_role_key="service-role-key",
		)

		def fake_urlopen(req, timeout):
			body = json.dumps([{"company_id": "company-1"}]).encode("utf-8")
			return _FakeResponse(status=200, body=body)

		with patch("src.route_resolver.request.urlopen", side_effect=fake_urlopen):
			with self.assertRaises(RouteResolverError):
				resolver.resolve("bad@farin.app")


if __name__ == "__main__":
	unittest.main()
