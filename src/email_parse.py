import logging
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path

logger = logging.getLogger(__name__)

EXTENSION_MIME_MAP = {
	".pdf": "application/pdf",
	".jpg": "image/jpeg",
	".jpeg": "image/jpeg",
	".png": "image/png",
	".webp": "image/webp",
	".heic": "image/heic",
	".heif": "image/heif",
	".doc": "application/msword",
	".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
	".txt": "text/plain",
	".csv": "text/csv"
}

RECIPIENT_HEADERS = (
	"X-Original-To",
	"Delivered-To",
	"Envelope-To",
	"X-Envelope-To",
	"To",
)


class ParsedEmail:
	def __init__(self, metadata, body_text, body_html, attachments):
		self.metadata = metadata
		self.body_text = body_text
		self.body_html = body_html
		self.attachments = attachments


def _normalize_addresses(value):
	if not value:
		return ""
	addresses = getaddresses([value])
	return ", ".join([addr for _, addr in addresses if addr])


def _infer_mime_type(filename, content_type):
	if content_type and content_type != "application/octet-stream":
		return content_type
	if not filename:
		return content_type or "application/octet-stream"
	ext = Path(filename).suffix.lower()
	return EXTENSION_MIME_MAP.get(ext, content_type or "application/octet-stream")


def _truncate(value, max_chars):
	if not value:
		return ""
	if len(value) <= max_chars:
		return value
	return value[:max_chars]


def _extract_header_addresses(message, header_name):
	values = message.get_all(header_name, [])
	addresses = []
	for value in values:
		for _, addr in getaddresses([value]):
			if addr:
				addresses.append(addr.strip().lower())
	return addresses


def parse_email(raw_bytes, max_body_chars):
	message = BytesParser(policy=policy.default).parsebytes(raw_bytes)

	message_id = message.get("Message-ID")
	subject = message.get("Subject")
	from_address = _normalize_addresses(message.get("From"))
	to_address = _normalize_addresses(message.get("To"))
	cc = _normalize_addresses(message.get("Cc"))
	reply_to = _normalize_addresses(message.get("Reply-To"))
	return_path = _normalize_addresses(message.get("Return-Path"))
	recipient_header_candidates = {}
	for header_name in RECIPIENT_HEADERS:
		candidates = _extract_header_addresses(message, header_name)
		if candidates:
			recipient_header_candidates[header_name.lower()] = candidates
	date_header = message.get("Date")
	received_date = None
	if date_header:
		try:
			received_date = parsedate_to_datetime(date_header).isoformat()
		except (TypeError, ValueError):
			received_date = None

	body_text = ""
	body_html = ""
	attachments = []

	if message.is_multipart():
		for part in message.walk():
			if part.is_multipart():
				continue
			content_disposition = (part.get("Content-Disposition") or "").lower()
			filename = part.get_filename()
			content_type = part.get_content_type()

			is_attachment = "attachment" in content_disposition
			is_inline = "inline" in content_disposition

			if content_type in ("text/plain", "text/html") and not is_attachment:
				payload = part.get_content()
				if content_type == "text/plain" and not body_text:
					body_text = payload
				elif content_type == "text/html" and not body_html:
					body_html = payload
				continue

			if not is_attachment and not filename and is_inline:
				continue

			payload = part.get_payload(decode=True)
			if payload is None:
				continue
			attachments.append(
				{
					"filename": filename,
					"content_type": _infer_mime_type(filename, content_type),
					"payload": payload,
					"size": len(payload)
				}
			)
	else:
		content_type = message.get_content_type()
		payload = message.get_content()
		if content_type == "text/plain":
			body_text = payload
		elif content_type == "text/html":
			body_html = payload
		else:
			logger.info("unsupported_singlepart content_type=%s", content_type)

	metadata = {
		"source": "email",
		"received_date": received_date,
		"message_id": message_id,
		"return_path": return_path,
		"reply_to": reply_to,
		"cc": cc,
		"recipient_header_candidates": recipient_header_candidates,
		"headers_subset": {
			"message_id": message_id,
			"subject": subject,
			"from": from_address,
			"to": to_address,
			"date": date_header
		}
	}

	return ParsedEmail(
		metadata=metadata,
		body_text=_truncate(body_text, max_body_chars),
		body_html=_truncate(body_html, max_body_chars),
		attachments=attachments
	)
