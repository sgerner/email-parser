import json
import logging
import uuid
from urllib import request
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class WebhookError(Exception):
	pass


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
		add_line(
			f'Content-Disposition: form-data; name="files"; filename="{filename}"'
		)
		add_line(f"Content-Type: {content_type}")
		add_line("")
		lines.append(item.get("data", b""))

	add_line(f"--{boundary}--")
	body = b"\r\n".join(lines)
	return body, boundary


def post_webhook(config, payload, attachments, timeout=30):
	headers = {
		"X-Accounting-Automation-Secret": config.accounting_automation_secret,
		"User-Agent": "Accounting-Ingest/1.0"
	}

	parsed = urlparse(config.webhook_url)
	if parsed.scheme and parsed.netloc:
		origin = f"{parsed.scheme}://{parsed.netloc}"
		headers["Origin"] = origin
		headers["Referer"] = origin

	if attachments:
		fields = {
			"external_event_id": payload.get("external_event_id"),
			"from_address": payload.get("from_address"),
			"to_address": payload.get("to_address"),
			"subject": payload.get("subject"),
			"body_text": payload.get("body_text"),
			"body_html": payload.get("body_html"),
			"metadata": json.dumps(payload.get("metadata") or {})
		}
		files = []
		for attachment in attachments:
			files.append(
				{
					"filename": attachment.get("filename"),
					"content_type": attachment.get("content_type"),
					"data": attachment.get("payload")
				}
			)
		body, boundary = _encode_multipart(fields, files)
		headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
	else:
		body = json.dumps(payload).encode("utf-8")
		headers["Content-Type"] = "application/json"

	req = request.Request(config.webhook_url, data=body, headers=headers, method="POST")
	try:
		with request.urlopen(req, timeout=timeout) as resp:
			response_body = resp.read().decode("utf-8")
			return resp.status, response_body
	except request.HTTPError as err:
		raise WebhookError(f"HTTP {err.code}: {err.read().decode('utf-8')}")
	except Exception as exc:
		raise WebhookError(str(exc))
