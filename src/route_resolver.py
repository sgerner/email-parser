import json
import logging
from dataclasses import dataclass
from urllib import request

logger = logging.getLogger(__name__)


class RouteResolverError(Exception):
	pass


@dataclass
class ResolvedRoute:
	company_id: str
	channel: str
	email_address: str


def _normalize_email(value):
	if not value:
		return ""
	return str(value).strip().strip("<>").lower()


class SupabaseRouteResolver:
	def __init__(self, supabase_url, service_role_key, timeout_seconds=10):
		self.supabase_url = (supabase_url or "").strip().rstrip("/")
		self.service_role_key = (service_role_key or "").strip()
		self.timeout_seconds = timeout_seconds or 10
		self._cache = {}

		if not self.supabase_url:
			raise RouteResolverError("Missing SUPABASE_URL")
		if not self.service_role_key:
			raise RouteResolverError("Missing SUPABASE_SERVICE_ROLE_KEY")

	def resolve(self, recipient_email):
		normalized = _normalize_email(recipient_email)
		if not normalized:
			return None
		if normalized in self._cache:
			return self._cache[normalized]

		url = f"{self.supabase_url}/rest/v1/rpc/resolve_company_inbound_email_route"
		headers = {
			"apikey": self.service_role_key,
			"Authorization": f"Bearer {self.service_role_key}",
			"Content-Type": "application/json",
			"Accept": "application/json",
			"User-Agent": "Accounting-Ingest/1.0",
		}
		payload = json.dumps({"p_recipient_email": normalized}).encode("utf-8")
		req = request.Request(url, data=payload, headers=headers, method="POST")

		try:
			with request.urlopen(req, timeout=self.timeout_seconds) as resp:
				body = resp.read().decode("utf-8")
				data = json.loads(body or "[]")
		except Exception as err:
			raise RouteResolverError(str(err)) from err

		route = None
		if isinstance(data, list) and data:
			item = data[0]
			company_id = (item.get("company_id") or "").strip()
			channel = (item.get("channel") or "").strip().lower()
			email_address = _normalize_email(item.get("email_address"))
			if company_id and channel in {"po", "accounting"} and email_address:
				route = ResolvedRoute(
					company_id=company_id,
					channel=channel,
					email_address=email_address,
				)
			else:
				raise RouteResolverError(f"Invalid route payload for recipient={normalized}")
		else:
			logger.warning("route_not_found recipient=%s", normalized)

		self._cache[normalized] = route
		return route
