import argparse
import hashlib
import imaplib
import logging
import sys
from dataclasses import dataclass
from email.utils import getaddresses

from .config import load_config
from .email_parse import parse_email
from .imap_client import (
	connect,
	ensure_mailbox,
	select_mailbox,
	search_uids,
	fetch_message,
	move_message
)
from .route_resolver import RouteResolverError, SupabaseRouteResolver
from .webhook_client import post_po_webhook, post_accounting_webhook, WebhookError

logger = logging.getLogger(__name__)


IMAGE_MIME_TYPES = {
	"image/jpeg",
	"image/png",
	"image/webp",
	"image/heic",
	"image/heif"
}
DOC_MIME_TYPES = {
	"application/msword",
	"application/vnd.openxmlformats-officedocument.wordprocessingml.document"
}
TEXT_MIME_TYPES = {
	"text/plain",
	"text/csv",
	"application/csv"
}


@dataclass
class MailRoute:
	name: str
	target: str
	imap_username: str
	imap_password: str
	imap_mailbox: str
	allowed_mime_types: list


@dataclass
class MessageRoute:
	target: str
	company_id: str | None
	recipient_email: str | None


def setup_logging():
	logging.basicConfig(
		level=logging.INFO,
		format="%(asctime)s %(levelname)s %(name)s %(message)s"
	)


def build_source_ref(parsed, raw_bytes):
	message_id = parsed.metadata.get("message_id")
	if message_id:
		return message_id
	fallback = hashlib.sha256(raw_bytes).hexdigest()
	return f"sha256:{fallback}"


def _attachment_priority(content_type):
	if content_type == "application/pdf":
		return 0
	if content_type in IMAGE_MIME_TYPES:
		return 1
	if content_type in DOC_MIME_TYPES:
		return 2
	if content_type in TEXT_MIME_TYPES:
		return 3
	return None


def _build_skipped_attachment(attachment, content_type, reason):
	return {
		"filename": attachment.get("filename") or "",
		"content_type": content_type,
		"size": attachment.get("size") or 0,
		"reason": reason
	}


def _normalize_email(value):
	if not value:
		return None
	email_value = str(value).strip().strip("<>").lower()
	return email_value or None


def _extract_fallback_to_recipients(parsed):
	to_value = parsed.metadata.get("headers_subset", {}).get("to") or ""
	addresses = [addr.strip().lower() for _, addr in getaddresses([to_value]) if addr]
	return addresses


def _resolve_recipient_email(parsed, header_priority):
	candidates = parsed.metadata.get("recipient_header_candidates") or {}
	for header_name in header_priority:
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


def _allowed_mime_types_for_target(config, target):
	if target == "po":
		return config.po_allowed_mime_types
	if target == "accounting":
		return config.accounting_allowed_mime_types
	return []


def _build_message_route(config, parsed, route_target, route_resolver=None):
	if route_target in {"po", "accounting"}:
		return MessageRoute(
			target=route_target,
			company_id=config.company_id,
			recipient_email=_normalize_email(parsed.metadata.get("headers_subset", {}).get("to"))
		)

	recipient_email = _resolve_recipient_email(parsed, config.recipient_header_priority)
	if not recipient_email:
		raise RouteResolverError("Unable to resolve recipient email from headers")
	if route_resolver is None:
		raise RouteResolverError("Dynamic route resolver is not configured")

	resolved = route_resolver.resolve(recipient_email)
	if resolved is None:
		raise RouteResolverError(f"No company route found for recipient={recipient_email}")

	return MessageRoute(
		target=resolved.channel,
		company_id=resolved.company_id,
		recipient_email=recipient_email
	)


def _move_unroutable_message(client, config, uid, keep_in_inbox=False, dry_run=False):
	if keep_in_inbox or dry_run:
		return
	fallback_mailbox = config.quarantine_mailbox or config.failed_mailbox
	if not fallback_mailbox:
		return
	move_message(client, uid, fallback_mailbox)


def select_attachment_for_webhook(attachments, allowed_mime_types, max_bytes):
	allowed_mime_set = set(allowed_mime_types)
	candidates = []
	skipped = []
	for index, attachment in enumerate(attachments):
		content_type = (attachment.get("content_type") or "").lower()
		size = attachment.get("size") or 0
		if size > max_bytes:
			skipped.append(_build_skipped_attachment(attachment, content_type, "too_large"))
			continue
		priority = _attachment_priority(content_type)
		if content_type not in allowed_mime_set or priority is None:
			skipped.append(_build_skipped_attachment(attachment, content_type, "unsupported_type"))
			continue
		candidates.append((priority, index, attachment))

	if not candidates:
		return None, skipped

	candidates.sort(key=lambda item: (item[0], item[1]))
	selected_attachment = dict(candidates[0][2])
	for _, _, non_selected in candidates[1:]:
		skipped.append(_build_skipped_attachment(non_selected, (non_selected.get("content_type") or "").lower(), "single_file_only"))
	return selected_attachment, skipped


def filter_attachments(attachments, allowed_mime_types, max_bytes):
	allowed_mime_set = set(allowed_mime_types)
	allowed = []
	skipped = []
	for attachment in attachments:
		content_type = (attachment.get("content_type") or "").lower()
		size = attachment.get("size") or 0
		if size > max_bytes:
			skipped.append(_build_skipped_attachment(attachment, content_type, "too_large"))
			continue
		if content_type not in allowed_mime_set:
			skipped.append(_build_skipped_attachment(attachment, content_type, "unsupported_type"))
			continue
		allowed.append(attachment)
	return allowed, skipped


def _base_payload(parsed):
	return {
		"from_address": parsed.metadata.get("headers_subset", {}).get("from"),
		"to_address": parsed.metadata.get("headers_subset", {}).get("to"),
		"subject": parsed.metadata.get("headers_subset", {}).get("subject"),
		"body_text": parsed.body_text or None,
		"body_html": parsed.body_html or None,
	}


def send_po_message(config, parsed, raw_bytes, po_attachment, skipped, company_id=None, recipient_email=None, dry_run=False):
	source_ref = build_source_ref(parsed, raw_bytes)
	payload = {
		**_base_payload(parsed),
		"source_ref": source_ref,
		"source_type": "email",
		"metadata": {
			**(parsed.metadata or {}),
			"attachments_count": len(parsed.attachments),
			"attachments_skipped": skipped,
			"attachments_skipped_count": len(skipped)
		}
	}
	if recipient_email:
		payload["metadata"]["routing_recipient"] = recipient_email

	if dry_run:
		logger.info(
			"dry_run_webhook target=po source_ref=%s company_id=%s recipient=%s has_attachment=%s",
			source_ref,
			company_id,
			recipient_email,
			bool(po_attachment)
		)
		return True

	status, response_body = post_po_webhook(config, payload, po_attachment, company_id=company_id)
	logger.info("webhook_response target=po status=%s source_ref=%s company_id=%s", status, source_ref, company_id)
	if response_body:
		logger.debug("webhook_body target=po source_ref=%s body=%s", source_ref, response_body)
	return 200 <= status < 300


def send_accounting_message(config, parsed, raw_bytes, attachments, skipped, company_id=None, recipient_email=None, dry_run=False):
	source_ref = build_source_ref(parsed, raw_bytes)
	payload = {
		**_base_payload(parsed),
		"external_event_id": source_ref,
		"metadata": {
			**(parsed.metadata or {}),
			"attachments_count": len(attachments),
			"attachments_skipped": skipped,
			"attachments_skipped_count": len(skipped)
		}
	}
	if recipient_email:
		payload["metadata"]["routing_recipient"] = recipient_email

	if dry_run:
		logger.info(
			"dry_run_webhook target=accounting source_ref=%s company_id=%s recipient=%s attachments=%s",
			source_ref,
			company_id,
			recipient_email,
			len(attachments)
		)
		return True

	status, response_body = post_accounting_webhook(config, payload, attachments, company_id=company_id)
	logger.info("webhook_response target=accounting status=%s source_ref=%s company_id=%s", status, source_ref, company_id)
	if response_body:
		logger.debug("webhook_body target=accounting source_ref=%s body=%s", source_ref, response_body)
	return 200 <= status < 300


def process_mailbox(
	client,
	config,
	mailbox,
	limit,
	target,
	allowed_mime_types,
	route_resolver=None,
	dry_run=False,
	keep_in_inbox=False,
	process_all=False
):
	select_mailbox(client, mailbox)
	criteria = "ALL" if process_all else "UNSEEN"
	uids = search_uids(client, criteria)
	if limit:
		uids = uids[:limit]
	logger.info("mailbox_scan target=%s mailbox=%s count=%s", target, mailbox, len(uids))

	for uid in uids:
		active_target = target
		try:
			raw_bytes = fetch_message(client, uid)
			parsed = parse_email(raw_bytes, config.max_body_chars)
			message_route = _build_message_route(config, parsed, target, route_resolver=route_resolver)
			active_target = message_route.target

			per_message_allowed_mime_types = allowed_mime_types or _allowed_mime_types_for_target(config, active_target)
			attachments, skipped = filter_attachments(
				parsed.attachments,
				per_message_allowed_mime_types,
				config.max_attachment_bytes
			)
			for attachment in attachments:
				payload = attachment.get("payload")
				attachment["sha256"] = hashlib.sha256(payload).hexdigest() if payload else None

			if active_target == "po":
				po_attachment, po_selection_skipped = select_attachment_for_webhook(
					attachments,
					per_message_allowed_mime_types,
					config.max_attachment_bytes
				)
				all_skipped = skipped + po_selection_skipped
				logger.info(
					"message_parsed target=%s uid=%s message_id=%s company_id=%s recipient=%s attachments_total=%s attachments_allowed=%s attachment_selected=%s skipped=%s",
					active_target,
					uid.decode() if isinstance(uid, bytes) else uid,
					parsed.metadata.get("message_id"),
					message_route.company_id,
					message_route.recipient_email,
					len(parsed.attachments),
					len(attachments),
					bool(po_attachment),
					len(all_skipped)
				)
				success = send_po_message(
					config,
					parsed,
					raw_bytes,
					po_attachment,
					all_skipped,
					company_id=message_route.company_id,
					recipient_email=message_route.recipient_email,
					dry_run=dry_run
				)
			else:
				logger.info(
					"message_parsed target=%s uid=%s message_id=%s company_id=%s recipient=%s attachments_total=%s attachments_allowed=%s skipped=%s",
					active_target,
					uid.decode() if isinstance(uid, bytes) else uid,
					parsed.metadata.get("message_id"),
					message_route.company_id,
					message_route.recipient_email,
					len(parsed.attachments),
					len(attachments),
					len(skipped)
				)
				success = send_accounting_message(
					config,
					parsed,
					raw_bytes,
					attachments,
					skipped,
					company_id=message_route.company_id,
					recipient_email=message_route.recipient_email,
					dry_run=dry_run
				)

			if keep_in_inbox or dry_run:
				continue
			if success:
				moved = move_message(client, uid, config.processed_mailbox)
				if moved:
					logger.info("message_moved target=%s uid=%s mailbox=%s", active_target, uid, config.processed_mailbox)
				else:
					logger.warning("message_move_failed target=%s uid=%s mailbox=%s", active_target, uid, config.processed_mailbox)
			else:
				moved = move_message(client, uid, config.failed_mailbox)
				if moved:
					logger.warning("message_failed target=%s uid=%s mailbox=%s", active_target, uid, config.failed_mailbox)
				else:
					logger.warning("message_move_failed target=%s uid=%s mailbox=%s", active_target, uid, config.failed_mailbox)
		except RouteResolverError as err:
			logger.error("route_resolution_failed uid=%s error=%s", uid, err)
			_move_unroutable_message(client, config, uid, keep_in_inbox=keep_in_inbox, dry_run=dry_run)
		except WebhookError as err:
			logger.error("webhook_failed target=%s uid=%s error=%s", active_target, uid, err)
			if not keep_in_inbox and not dry_run:
				move_message(client, uid, config.failed_mailbox)
		except Exception as err:
			logger.exception("message_processing_error target=%s uid=%s error=%s", active_target, uid, err)
			if not keep_in_inbox and not dry_run:
				move_message(client, uid, config.failed_mailbox)


def build_routes(config, mailbox_override=None):
	routes = []
	use_supabase_routes = getattr(config, "use_supabase_routes", False)
	if use_supabase_routes:
		if config.imap_username and config.imap_password:
			routes.append(
				MailRoute(
					name="dynamic",
					target="dynamic",
					imap_username=config.imap_username,
					imap_password=config.imap_password,
					imap_mailbox=mailbox_override or config.imap_mailbox,
					allowed_mime_types=[],
				)
			)
		else:
			logger.warning("route_skipped target=dynamic reason=missing_imap_credentials")
		return routes

	if config.accounting_webhook_url:
		if config.accounting_imap_username and config.accounting_imap_password:
			routes.append(
				MailRoute(
					name="accounting",
					target="accounting",
					imap_username=config.accounting_imap_username,
					imap_password=config.accounting_imap_password,
					imap_mailbox=mailbox_override or config.accounting_imap_mailbox,
					allowed_mime_types=config.accounting_allowed_mime_types,
				)
			)
		else:
			logger.warning("route_skipped target=accounting reason=missing_imap_credentials")

	if config.po_webhook_url:
		if config.po_imap_username and config.po_imap_password:
			routes.append(
				MailRoute(
					name="po",
					target="po",
					imap_username=config.po_imap_username,
					imap_password=config.po_imap_password,
					imap_mailbox=mailbox_override or config.po_imap_mailbox,
					allowed_mime_types=config.po_allowed_mime_types,
				)
			)
		else:
			logger.warning("route_skipped target=po reason=missing_imap_credentials")

	return routes


def build_parser():
	parser = argparse.ArgumentParser(description="Accounting email ingest runner")
	parser.add_argument("--run-once", action="store_true", help="Process inbox and exit")
	parser.add_argument("--mailbox", default=None, help="Mailbox to process")
	parser.add_argument("--limit", type=int, default=None, help="Max messages to process")
	parser.add_argument("--retry-failed", action="store_true", help="Process failed mailbox")
	parser.add_argument("--dry-run", action="store_true", help="Log actions without posting or moving")
	parser.add_argument("--keep-in-inbox", action="store_true", help="Do not move messages")
	parser.add_argument("--process-all", action="store_true", help="Process all messages, not just UNSEEN")
	parser.add_argument("--config", default=None, help="Path to env file")
	return parser


def main():
	setup_logging()
	parser = build_parser()
	args = parser.parse_args()

	config = load_config(args.config or None)
	use_supabase_routes = getattr(config, "use_supabase_routes", False)
	if not config.accounting_automation_secret:
		logger.error("missing_required_config secret_set=%s", bool(config.accounting_automation_secret))
		sys.exit(1)
	if use_supabase_routes:
		if not config.po_webhook_url or not config.accounting_webhook_url:
			logger.error(
				"missing_required_config dynamic_routing=true po_webhook_url_set=%s accounting_webhook_url_set=%s",
				bool(config.po_webhook_url),
				bool(config.accounting_webhook_url)
			)
			sys.exit(1)
		if not getattr(config, "supabase_url", "") or not getattr(config, "supabase_service_role_key", ""):
			logger.error(
				"missing_required_config dynamic_routing=true supabase_url_set=%s supabase_service_role_key_set=%s",
				bool(getattr(config, "supabase_url", "")),
				bool(getattr(config, "supabase_service_role_key", ""))
			)
			sys.exit(1)
	else:
		if not config.po_webhook_url and not config.accounting_webhook_url:
			logger.error("missing_required_config po_webhook_url=%s accounting_webhook_url=%s", config.po_webhook_url, config.accounting_webhook_url)
			sys.exit(1)

	route_resolver = None
	if use_supabase_routes:
		try:
			route_resolver = SupabaseRouteResolver(
				supabase_url=getattr(config, "supabase_url", ""),
				service_role_key=getattr(config, "supabase_service_role_key", ""),
				timeout_seconds=getattr(config, "supabase_timeout_seconds", 10)
			)
		except RouteResolverError as err:
			logger.error("route_resolver_init_failed error=%s", err)
			sys.exit(1)

	routes = build_routes(config, mailbox_override=args.mailbox)
	if not routes:
		logger.error("missing_imap_credentials_for_configured_routes")
		sys.exit(1)

	limit = args.limit or config.poll_limit
	retry_failed = args.retry_failed or config.retry_failed
	dry_run = args.dry_run or config.dry_run
	process_all = args.process_all or config.process_all

	route_failures = 0
	for route in routes:
		logger.info(
			"route_start target=%s username=%s mailbox=%s",
			route.target,
			route.imap_username,
			route.imap_mailbox,
		)
		try:
			client = connect(config, username=route.imap_username, password=route.imap_password)
		except imaplib.IMAP4.error as err:
			route_failures += 1
			logger.error(
				"route_authentication_failed target=%s username=%s error=%s",
				route.target,
				route.imap_username,
				err,
			)
			continue
		except Exception as err:
			route_failures += 1
			logger.exception(
				"route_connect_failed target=%s username=%s error=%s",
				route.target,
				route.imap_username,
				err,
			)
			continue
		try:
			ensure_mailbox(client, config.processed_mailbox)
			ensure_mailbox(client, config.failed_mailbox)
			if config.quarantine_mailbox:
				ensure_mailbox(client, config.quarantine_mailbox)

			process_mailbox(
				client,
				config,
				route.imap_mailbox,
				limit,
				target=route.target,
				allowed_mime_types=route.allowed_mime_types,
				route_resolver=route_resolver,
				dry_run=dry_run,
				keep_in_inbox=args.keep_in_inbox,
				process_all=process_all,
			)

			if retry_failed:
				process_mailbox(
					client,
					config,
					config.failed_mailbox,
					limit,
					target=route.target,
					allowed_mime_types=route.allowed_mime_types,
					route_resolver=route_resolver,
					dry_run=dry_run,
					keep_in_inbox=args.keep_in_inbox,
					process_all=True,
				)
		except Exception as err:
			route_failures += 1
			logger.exception(
				"route_processing_failed target=%s mailbox=%s error=%s",
				route.target,
				route.imap_mailbox,
				err,
			)
		finally:
			try:
				client.logout()
			except Exception:
				pass

	if route_failures:
		sys.exit(1)


if __name__ == "__main__":
	main()
