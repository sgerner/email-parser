import json
import logging
import uuid
from urllib import request
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class WebhookError(Exception):
	pass


def _resolved_company_id(config, company_id=None):
	return company_id or getattr(config, "company_id", None)


def _build_default_headers(secret):
	return {
		"X-Accounting-Automation-Secret": secret,
		"User-Agent": "Accounting-Ingest/1.0"
	}


def _add_origin_headers(url, headers):
	parsed = urlparse(url)
	if parsed.scheme and parsed.netloc:
		origin = f"{parsed.scheme}://{parsed.netloc}"
		headers["Origin"] = origin
		headers["Referer"] = origin


def _post_request(url, headers, body, timeout=30):
	req = request.Request(url, data=body, headers=headers, method="POST")
	try:
		with request.urlopen(req, timeout=timeout) as resp:
			response_body = resp.read().decode("utf-8")
			return resp.status, response_body
	except request.HTTPError as err:
		raise WebhookError(f"HTTP {err.code}: {err.read().decode('utf-8')}")
	except Exception as exc:
		raise WebhookError(str(exc))


def _encode_multipart(fields, files):
	boundary = f"----acct-ingest-{uuid.uuid4().hex}"
	lines = []

	def add_line(line):
		lines.append(line.encode("utf-8"))

	for key, value in fields.items():
		if value is None:
			continue
		add_line(f"--{boundary}")
		add_line(f'Content-Disposition: form-data; name="{key}"')
		add_line("")
		add_line(str(value))

	for item in files:
		add_line(f"--{boundary}")
		filename = item.get("filename") or "attachment"
		content_type = item.get("content_type") or "application/octet-stream"
		field_name = item.get("field_name") or "file"
		add_line(
			f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"'
		)
		add_line(f"Content-Type: {content_type}")
		add_line("")
		lines.append(item.get("data", b""))

	add_line(f"--{boundary}--")
	body = b"\r\n".join(lines)
	return body, boundary


def post_po_webhook(config, payload, attachment, timeout=180, company_id=None):
	if not config.po_webhook_url:
		raise WebhookError("Missing po_webhook_url")

	url = config.po_webhook_url
	url += "&async=true" if "?" in url else "?async=true"

	headers = _build_default_headers(config.accounting_automation_secret)
	resolved_company_id = _resolved_company_id(config, company_id=company_id)
	if resolved_company_id:
		headers["X-Company-Id"] = resolved_company_id
	_add_origin_headers(url, headers)
	if attachment:
		metadata = payload.get("metadata") or {}
		if resolved_company_id:
			metadata["company_id"] = resolved_company_id
		fields = {
			"from_address": payload.get("from_address"),
			"to_address": payload.get("to_address"),
			"subject": payload.get("subject"),
			"body_text": payload.get("body_text"),
			"body_html": payload.get("body_html"),
			"source_ref": payload.get("source_ref"),
			"source_type": payload.get("source_type"),
			"metadata": json.dumps(metadata)
		}
		files = [
			{
				"field_name": "file",
				"filename": attachment.get("filename"),
				"content_type": attachment.get("content_type"),
				"data": attachment.get("payload")
			}
		]
		body, boundary = _encode_multipart(fields, files)
		headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
	else:
		if resolved_company_id:
			payload.setdefault("metadata", {})["company_id"] = resolved_company_id
		body = json.dumps(payload).encode("utf-8")
		headers["Content-Type"] = "application/json"
	return _post_request(url, headers, body, timeout=timeout)


def post_accounting_webhook(config, payload, attachments, timeout=30, company_id=None):
	if not config.accounting_webhook_url:
		raise WebhookError("Missing accounting_webhook_url")
	headers = _build_default_headers(config.accounting_automation_secret)
	resolved_company_id = _resolved_company_id(config, company_id=company_id)
	if resolved_company_id:
		headers["X-Company-Id"] = resolved_company_id
	_add_origin_headers(config.accounting_webhook_url, headers)
	if attachments:
		fields = {
			"company_id": resolved_company_id,
			"external_event_id": payload.get("external_event_id"),
			"from_address": payload.get("from_address"),
			"to_address": payload.get("to_address"),
			"subject": payload.get("subject"),
			"body_text": payload.get("body_text"),
			"body_html": payload.get("body_html"),
			"metadata": json.dumps(payload.get("metadata") or {})
		}
		files = [
			{
				"field_name": "files",
				"filename": attachment.get("filename"),
				"content_type": attachment.get("content_type"),
				"data": attachment.get("payload")
			}
			for attachment in attachments
		]
		body, boundary = _encode_multipart(fields, files)
		headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
	else:
		if resolved_company_id:
			payload["company_id"] = resolved_company_id
		body = json.dumps(payload).encode("utf-8")
		headers["Content-Type"] = "application/json"
	return _post_request(config.accounting_webhook_url, headers, body, timeout=timeout)


def post_webhook(config, payload, attachment, timeout=30, company_id=None):
	# Backward-compatible helper used by existing callers/tests.
	return post_po_webhook(config, payload, attachment, timeout=timeout, company_id=company_id)
