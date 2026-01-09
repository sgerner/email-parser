import argparse
import hashlib
import json
import logging
import sys

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
from .webhook_client import post_webhook, WebhookError

logger = logging.getLogger(__name__)


def setup_logging():
	logging.basicConfig(
		level=logging.INFO,
		format="%(asctime)s %(levelname)s %(name)s %(message)s"
	)


def build_external_event_id(parsed, raw_bytes):
	message_id = parsed.metadata.get("message_id")
	if message_id:
		return message_id
	fallback = hashlib.sha256(raw_bytes).hexdigest()
	return f"sha256:{fallback}"


def filter_attachments(attachments, allowed_mime_types, max_bytes):
	allowed = []
	skipped = []
	for attachment in attachments:
		content_type = (attachment.get("content_type") or "").lower()
		filename = attachment.get("filename") or ""
		size = attachment.get("size") or 0
		if size > max_bytes:
			skipped.append({
				"filename": filename,
				"content_type": content_type,
				"size": size,
				"reason": "too_large"
			})
			continue
		if content_type not in allowed_mime_types:
			skipped.append({
				"filename": filename,
				"content_type": content_type,
				"size": size,
				"reason": "unsupported_type"
			})
			continue
		allowed.append(attachment)
	return allowed, skipped


def send_message(config, parsed, raw_bytes, attachments, skipped, dry_run=False):
	external_event_id = build_external_event_id(parsed, raw_bytes)
	payload = {
		"external_event_id": external_event_id,
		"from_address": parsed.metadata.get("headers_subset", {}).get("from"),
		"to_address": parsed.metadata.get("headers_subset", {}).get("to"),
		"subject": parsed.metadata.get("headers_subset", {}).get("subject"),
		"body_text": parsed.body_text or None,
		"body_html": parsed.body_html or None,
		"metadata": {
			**(parsed.metadata or {}),
			"attachments_skipped": skipped[:10],
			"attachments_count": len(attachments),
			"attachments_skipped_count": len(skipped)
		}
	}

	if dry_run:
		logger.info(
			"dry_run_webhook message_id=%s attachments=%s",
			external_event_id,
			len(attachments)
		)
		return True

	status, response_body = post_webhook(config, payload, attachments)
	logger.info("webhook_response status=%s message_id=%s", status, external_event_id)
	if response_body:
		logger.debug("webhook_body message_id=%s body=%s", external_event_id, response_body)
	return 200 <= status < 300


def process_mailbox(client, config, mailbox, limit, dry_run=False, keep_in_inbox=False, process_all=False):
	select_mailbox(client, mailbox)
	criteria = "ALL" if process_all else "UNSEEN"
	uids = search_uids(client, criteria)
	if limit:
		uids = uids[:limit]
	logger.info("mailbox_scan mailbox=%s count=%s", mailbox, len(uids))

	for uid in uids:
		try:
			raw_bytes = fetch_message(client, uid)
			parsed = parse_email(raw_bytes, config.max_body_chars)
			attachments, skipped = filter_attachments(
				parsed.attachments,
				config.allowed_mime_types,
				config.max_attachment_bytes
			)
			for attachment in attachments:
				payload = attachment.get("payload")
				attachment["sha256"] = hashlib.sha256(payload).hexdigest() if payload else None
			logger.info(
				"message_parsed uid=%s message_id=%s attachments=%s skipped=%s",
				uid.decode() if isinstance(uid, bytes) else uid,
				parsed.metadata.get("message_id"),
				len(attachments),
				len(skipped)
			)

			success = send_message(config, parsed, raw_bytes, attachments, skipped, dry_run=dry_run)
			if keep_in_inbox or dry_run:
				continue
			if success:
				moved = move_message(client, uid, config.processed_mailbox)
				if moved:
					logger.info("message_moved uid=%s target=%s", uid, config.processed_mailbox)
				else:
					logger.warning("message_move_failed uid=%s target=%s", uid, config.processed_mailbox)
			else:
				moved = move_message(client, uid, config.failed_mailbox)
				if moved:
					logger.warning("message_failed uid=%s target=%s", uid, config.failed_mailbox)
				else:
					logger.warning("message_move_failed uid=%s target=%s", uid, config.failed_mailbox)
		except WebhookError as err:
			logger.error("webhook_failed uid=%s error=%s", uid, err)
			if not keep_in_inbox and not dry_run:
				move_message(client, uid, config.failed_mailbox)
		except Exception as err:
			logger.exception("message_processing_error uid=%s error=%s", uid, err)
			if not keep_in_inbox and not dry_run:
				move_message(client, uid, config.failed_mailbox)


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
	if not config.webhook_url or not config.accounting_automation_secret:
		logger.error("missing_required_config webhook_url=%s secret_set=%s", config.webhook_url, bool(config.accounting_automation_secret))
		sys.exit(1)

	if not config.imap_username or not config.imap_password:
		logger.error("missing_imap_credentials")
		sys.exit(1)

	mailbox = args.mailbox or config.imap_mailbox
	limit = args.limit or config.poll_limit
	retry_failed = args.retry_failed or config.retry_failed
	dry_run = args.dry_run or config.dry_run
	process_all = args.process_all or config.process_all

	client = connect(config)
	try:
		ensure_mailbox(client, config.processed_mailbox)
		ensure_mailbox(client, config.failed_mailbox)
		if config.quarantine_mailbox:
			ensure_mailbox(client, config.quarantine_mailbox)

		process_mailbox(
			client,
			config,
			mailbox,
			limit,
			dry_run=dry_run,
			keep_in_inbox=args.keep_in_inbox,
			process_all=process_all
		)

		if retry_failed:
			process_mailbox(
				client,
				config,
				config.failed_mailbox,
				limit,
				dry_run=dry_run,
				keep_in_inbox=args.keep_in_inbox,
				process_all=True
			)
	finally:
		try:
			client.logout()
		except Exception:
			pass


if __name__ == "__main__":
	main()
