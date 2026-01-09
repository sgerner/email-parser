import imaplib
import logging
import re
import ssl

logger = logging.getLogger(__name__)


def connect(config):
	if config.imap_tls:
		context = ssl.create_default_context()
		if not config.imap_tls_verify:
			context.check_hostname = False
			context.verify_mode = ssl.CERT_NONE
		client = imaplib.IMAP4_SSL(config.imap_host, config.imap_port, ssl_context=context)
	else:
		client = imaplib.IMAP4(config.imap_host, config.imap_port)
	client.login(config.imap_username, config.imap_password)
	return client


def _extract_prefix(data):
	if not data:
		return None
	for item in data:
		if isinstance(item, bytes):
			text = item.decode("utf-8", errors="ignore")
			m = re.search(r"prefixed with:\s*([^\s)]+)", text)
			if m:
				return m.group(1)
	return None


def _normalized_mailbox(name):
	return name.replace("/", ".")


def ensure_mailbox(client, mailbox):
	if not mailbox:
		return
	name = _normalized_mailbox(mailbox)
	status, result = client.create(name)
	if status == "OK":
		logger.info("imap_mailbox_created mailbox=%s", name)
		return
	if isinstance(result, list) and result and b"[ALREADYEXISTS" in result[0]:
		logger.info("imap_mailbox_exists mailbox=%s", name)
		return
	prefix = _extract_prefix(result)
	if prefix:
		status, result = client.create(f"{prefix}{name}")
		if status == "OK":
			logger.info("imap_mailbox_created mailbox=%s", f"{prefix}{name}")
			return
	logger.warning(
		"imap_mailbox_create_failed mailbox=%s status=%s result=%s",
		name,
		status,
		result
	)


def select_mailbox(client, mailbox, readonly=False):
	name = _normalized_mailbox(mailbox)
	status, data = client.select(name, readonly=readonly)
	if status == "OK":
		return
	prefix = _extract_prefix(data)
	if prefix:
		status, data = client.select(f"{prefix}{name}", readonly=readonly)
		if status == "OK":
			return
	raise RuntimeError(f"Unable to select mailbox {mailbox}: {data}")


def supports_move(client):
	caps = client.capabilities or []
	return b"MOVE" in caps or "MOVE" in caps


def search_uids(client, criteria):
	status, data = client.uid("search", None, criteria)
	if status != "OK":
		return []
	if not data or not data[0]:
		return []
	return data[0].split()


def fetch_message(client, uid):
	status, data = client.uid("fetch", uid, "(RFC822)")
	if status != "OK" or not data:
		raise RuntimeError("Unable to fetch message")
	for item in data:
		if isinstance(item, tuple):
			return item[1]
	raise RuntimeError("Message payload missing")


def move_message(client, uid, mailbox):
	try:
		if supports_move(client):
			status, _ = client.uid("MOVE", uid, mailbox)
			if status == "OK":
				return True
			logger.warning("imap_move_failed uid=%s mailbox=%s status=%s", uid, mailbox, status)
		status, _ = client.uid("COPY", uid, mailbox)
		if status != "OK":
			logger.warning("imap_copy_failed uid=%s mailbox=%s status=%s", uid, mailbox, status)
			return False
		client.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
		client.expunge()
		return True
	except Exception as err:
		logger.warning("imap_move_exception uid=%s mailbox=%s error=%s", uid, mailbox, err)
		return False
