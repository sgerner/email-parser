import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ENV_PATH = "/etc/accounting-ingest/accounting-ingest.env"


def _parse_bool(value, default=False):
	if value is None:
		return default
	return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(value, default):
	try:
		return int(value)
	except (TypeError, ValueError):
		return default


def _parse_list(value, default=None):
	if value is None:
		return default or []
	return [item.strip().lower() for item in str(value).split(",") if item.strip()]


def load_env_file(path):
    env_path = Path(path)
    if not env_path.exists():
        return {}
    data = {}
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except PermissionError as err:
        raise PermissionError(f"Unable to read config {env_path}: {err}") from err
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip()
    return data


@dataclass
class Config:
	webhook_url: str
	accounting_automation_secret: str
	imap_host: str
	imap_port: int
	imap_tls: bool
	imap_tls_verify: bool
	imap_username: str
	imap_password: str
	imap_mailbox: str
	processed_mailbox: str
	failed_mailbox: str
	quarantine_mailbox: str | None
	allowed_mime_types: list
	max_attachment_bytes: int
	max_body_chars: int
	poll_limit: int
	retry_failed: bool
	dry_run: bool
	process_all: bool



def load_config(env_path=DEFAULT_ENV_PATH, overrides=None):
	env_path = env_path or DEFAULT_ENV_PATH
	file_env = load_env_file(env_path)
	for key, value in file_env.items():
		if key not in os.environ:
			os.environ[key] = value

	overrides = overrides or {}
	def env(key, fallback=None):
		return overrides.get(key, os.environ.get(key, fallback))

	return Config(
		webhook_url=env("WEBHOOK_URL", "").strip(),
		accounting_automation_secret=env("ACCOUNTING_AUTOMATION_SECRET", "").strip(),
		imap_host=env("IMAP_HOST", "127.0.0.1").strip(),
		imap_port=_parse_int(env("IMAP_PORT", "993"), 993),
		imap_tls=_parse_bool(env("IMAP_TLS", "true"), True),
		imap_tls_verify=_parse_bool(env("IMAP_TLS_VERIFY", "false"), False),
		imap_username=env("IMAP_USERNAME", "").strip(),
		imap_password=env("IMAP_PASSWORD", "").strip(),
		imap_mailbox=env("IMAP_MAILBOX", "INBOX").strip(),
		processed_mailbox=env("PROCESSED_MAILBOX", "INBOX.Archive.Processed").strip(),
		failed_mailbox=env("FAILED_MAILBOX", "INBOX.Archive.Failed").strip(),
		quarantine_mailbox=env("QUARANTINE_MAILBOX", "INBOX.Archive.Quarantine")
		.strip()
		or None,
		allowed_mime_types=_parse_list(
			env("ALLOWED_MIME_TYPES", "application/pdf,image/jpeg,image/png,image/heic")
		),
		max_attachment_bytes=_parse_int(env("MAX_ATTACHMENT_BYTES", "26214400"), 26214400),
		max_body_chars=_parse_int(env("MAX_BODY_CHARS", "20000"), 20000),
		poll_limit=_parse_int(env("POLL_LIMIT", "25"), 25),
		retry_failed=_parse_bool(env("RETRY_FAILED", "true"), True),
		dry_run=_parse_bool(env("DRY_RUN", "false"), False),
		process_all=_parse_bool(env("PROCESS_ALL", "false"), False)
	)
