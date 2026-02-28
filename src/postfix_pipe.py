#!/usr/bin/env python3
import argparse
import hashlib
import logging
import os
import sys
from email.utils import getaddresses

if __package__ in {None, ""}:
	sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import load_config
from src.email_parse import parse_email
from src.ingest import (
	filter_attachments,
	select_attachment_for_webhook,
	send_po_message,
	send_accounting_message,
)
from src.route_resolver import RouteResolverError, SupabaseRouteResolver
from src.webhook_client import WebhookError

logger = logging.getLogger(__name__)

EX_OK = 0
EX_NOUSER = 67
EX_SOFTWARE = 70
EX_TEMPFAIL = 75


class UnknownRecipientError(Exception):
	pass


def setup_logging(level="INFO"):
	logging.basicConfig(
		level=getattr(logging, str(level).upper(), logging.INFO),
		format="%(asctime)s %(levelname)s %(name)s %(message)s"
	)


def _normalize_email(value):
	if not value:
		return None
	normalized = str(value).strip().strip("<>").lower()
	return normalized or None


def _extract_fallback_to_recipients(parsed):
	to_value = parsed.metadata.get("headers_subset", {}).get("to") or ""
	return [addr.strip().lower() for _, addr in getaddresses([to_value]) if addr]


def _resolve_recipient_email(parsed, header_priority):
	candidates = parsed.metadata.get("recipient_header_candidates") or {}
	for header_name in header_priority or []:
		header_key = (header_name or "").strip().lower()
		if not header_key:
			continue
		for candidate in candidates.get(header_key, []):
			email_value = _normalize_email(candidate)
			if email_value:
				return email_value
	for candidate in _extract_fallback_to_recipients(parsed):
		email_value = _normalize_email(candidate)
		if email_value:
			return email_value
	return None


def _infer_target_from_recipient(recipient_email):
	normalized = _normalize_email(recipient_email)
	if not normalized or "@" not in normalized:
		return None
	local_part = normalized.split("@", 1)[0]
	if local_part in {"po", "accounting"}:
		return local_part
	if local_part.endswith("-po"):
		return "po"
	if local_part.endswith("-accounting"):
		return "accounting"
	return None


def _resolve_message_route(config, parsed, recipient_override, route_resolver=None):
	recipient_email = _normalize_email(recipient_override) or _resolve_recipient_email(
		parsed,
		getattr(config, "recipient_header_priority", [])
	)
	if not recipient_email:
		raise RouteResolverError("Unable to determine recipient email")

	if getattr(config, "use_supabase_routes", False):
		if route_resolver is None:
			raise RouteResolverError("Dynamic route resolver is not configured")
		resolved = route_resolver.resolve(recipient_email)
		if resolved is None:
			raise UnknownRecipientError(recipient_email)
		return resolved.channel, resolved.company_id, recipient_email

	target = _infer_target_from_recipient(recipient_email)
	if target is None:
		has_po = bool(getattr(config, "po_webhook_url", ""))
		has_accounting = bool(getattr(config, "accounting_webhook_url", ""))
		if has_po and not has_accounting:
			target = "po"
		elif has_accounting and not has_po:
			target = "accounting"
		else:
			raise RouteResolverError(f"Unable to infer static target from recipient={recipient_email}")

	if target == "po" and not getattr(config, "po_webhook_url", ""):
		raise RouteResolverError("PO target inferred but PO_WEBHOOK_URL is not configured")
	if target == "accounting" and not getattr(config, "accounting_webhook_url", ""):
		raise RouteResolverError("Accounting target inferred but ACCOUNTING_WEBHOOK_URL is not configured")

	return target, getattr(config, "company_id", None), recipient_email


def _allowed_mime_types_for_target(config, target):
	if target == "po":
		return config.po_allowed_mime_types
	if target == "accounting":
		return config.accounting_allowed_mime_types
	return []


def process_stdin_message(config, recipient_override=None, dry_run=False):
	raw_bytes = sys.stdin.buffer.read()
	if not raw_bytes:
		raise RouteResolverError("No message data received on stdin")

	parsed = parse_email(raw_bytes, config.max_body_chars)

	route_resolver = None
	if getattr(config, "use_supabase_routes", False):
		route_resolver = SupabaseRouteResolver(
			supabase_url=config.supabase_url,
			service_role_key=config.supabase_service_role_key,
			timeout_seconds=config.supabase_timeout_seconds
		)

	target, company_id, recipient_email = _resolve_message_route(
		config,
		parsed,
		recipient_override=recipient_override,
		route_resolver=route_resolver
	)
	allowed_mime_types = _allowed_mime_types_for_target(config, target)
	attachments, skipped = filter_attachments(
		parsed.attachments,
		allowed_mime_types,
		config.max_attachment_bytes
	)
	for attachment in attachments:
		payload = attachment.get("payload")
		attachment["sha256"] = hashlib.sha256(payload).hexdigest() if payload else None

	logger.info(
		"postfix_pipe_route target=%s company_id=%s recipient=%s message_id=%s attachments_total=%s attachments_allowed=%s",
		target,
		company_id,
		recipient_email,
		parsed.metadata.get("message_id"),
		len(parsed.attachments),
		len(attachments),
	)

	if target == "po":
		po_attachment, po_selection_skipped = select_attachment_for_webhook(
			attachments,
			allowed_mime_types,
			config.max_attachment_bytes
		)
		all_skipped = skipped + po_selection_skipped
		return send_po_message(
			config,
			parsed,
			raw_bytes,
			po_attachment,
			all_skipped,
			company_id=company_id,
			recipient_email=recipient_email,
			dry_run=dry_run
		)

	return send_accounting_message(
		config,
		parsed,
		raw_bytes,
		attachments,
		skipped,
		company_id=company_id,
		recipient_email=recipient_email,
		dry_run=dry_run
	)


def build_parser():
	parser = argparse.ArgumentParser(description="Postfix pipe helper for email ingest")
	parser.add_argument("--recipient", default=None, help="Envelope recipient (e.g. ${original_recipient})")
	parser.add_argument("--config", default=None, help="Path to env file")
	parser.add_argument("--dry-run", action="store_true", help="Log only, do not post webhooks")
	parser.add_argument(
		"--unknown-recipient-action",
		choices=("bounce", "tempfail", "discard"),
		default="bounce",
		help="Behavior when no route matches recipient"
	)
	parser.add_argument("--log-level", default="INFO", help="Logging level")
	return parser


def main():
	parser = build_parser()
	args = parser.parse_args()
	setup_logging(level=args.log_level)

	config = load_config(args.config or None)
	if not config.accounting_automation_secret:
		logger.error("missing_required_config secret_set=%s", bool(config.accounting_automation_secret))
		sys.exit(EX_SOFTWARE)
	if not config.po_webhook_url and not config.accounting_webhook_url:
		logger.error("missing_required_config po_webhook_url=%s accounting_webhook_url=%s", config.po_webhook_url, config.accounting_webhook_url)
		sys.exit(EX_SOFTWARE)
	if config.use_supabase_routes and (not config.supabase_url or not config.supabase_service_role_key):
		logger.error(
			"missing_required_config dynamic_routing=true supabase_url_set=%s supabase_service_role_key_set=%s",
			bool(config.supabase_url),
			bool(config.supabase_service_role_key)
		)
		sys.exit(EX_SOFTWARE)

	try:
		success = process_stdin_message(
			config,
			recipient_override=args.recipient,
			dry_run=args.dry_run
		)
		if success:
			sys.exit(EX_OK)
		logger.warning("postfix_pipe_webhook_non_2xx")
		sys.exit(EX_TEMPFAIL)
	except UnknownRecipientError as err:
		logger.warning("postfix_pipe_unknown_recipient recipient=%s action=%s", err, args.unknown_recipient_action)
		if args.unknown_recipient_action == "discard":
			sys.exit(EX_OK)
		if args.unknown_recipient_action == "tempfail":
			sys.exit(EX_TEMPFAIL)
		sys.exit(EX_NOUSER)
	except RouteResolverError as err:
		logger.error("postfix_pipe_route_error error=%s", err)
		sys.exit(EX_TEMPFAIL)
	except WebhookError as err:
		logger.error("postfix_pipe_webhook_error error=%s", err)
		sys.exit(EX_TEMPFAIL)
	except Exception as err:
		logger.exception("postfix_pipe_unhandled_error error=%s", err)
		sys.exit(EX_SOFTWARE)


if __name__ == "__main__":
	main()
