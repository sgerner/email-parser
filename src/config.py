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
	company_id: str | None
	use_supabase_routes: bool
	supabase_url: str
	supabase_service_role_key: str
	supabase_timeout_seconds: int
	recipient_header_priority: list
	po_webhook_url: str
	accounting_webhook_url: str
	accounting_automation_secret: str
	imap_host: str
	imap_port: int
	imap_tls: bool
	imap_tls_verify: bool
	imap_username: str
	imap_password: str
	imap_mailbox: str
	accounting_imap_username: str
	accounting_imap_password: str
	accounting_imap_mailbox: str
	po_imap_username: str
	po_imap_password: str
	po_imap_mailbox: str
	processed_mailbox: str
	failed_mailbox: str
	quarantine_mailbox: str | None
	accounting_allowed_mime_types: list
	po_allowed_mime_types: list
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

	legacy_webhook_url = env("WEBHOOK_URL", "").strip()
	po_webhook_url = env("PO_WEBHOOK_URL", "").strip()
	accounting_webhook_url = env("ACCOUNTING_WEBHOOK_URL", "").strip()
	if not po_webhook_url:
		if legacy_webhook_url and "/api/v1/customers/wholesale/orders/po-drafts" in legacy_webhook_url:
			po_webhook_url = legacy_webhook_url
	if not accounting_webhook_url and legacy_webhook_url and "/api/v1/accounting/automation/ingest" in legacy_webhook_url:
		accounting_webhook_url = legacy_webhook_url

	legacy_imap_username = env("IMAP_USERNAME", "").strip()
	legacy_imap_password = env("IMAP_PASSWORD", "").strip()
	legacy_imap_mailbox = env("IMAP_MAILBOX", "INBOX").strip()

	accounting_imap_username = env("ACCOUNTING_IMAP_USERNAME", legacy_imap_username).strip()
	accounting_imap_password = env("ACCOUNTING_IMAP_PASSWORD", legacy_imap_password).strip()
	accounting_imap_mailbox = env("ACCOUNTING_IMAP_MAILBOX", legacy_imap_mailbox).strip()
	po_imap_username = env("PO_IMAP_USERNAME", "").strip()
	po_imap_password = env("PO_IMAP_PASSWORD", "").strip()
	po_imap_mailbox = env("PO_IMAP_MAILBOX", "INBOX").strip()

	# Compatibility for existing single-mailbox PO setups using IMAP_* only.
	if po_webhook_url and not accounting_webhook_url and not po_imap_username and legacy_imap_username:
		po_imap_username = legacy_imap_username
	if po_webhook_url and not accounting_webhook_url and not po_imap_password and legacy_imap_password:
		po_imap_password = legacy_imap_password
	if po_webhook_url and not accounting_webhook_url and po_imap_mailbox == "INBOX" and legacy_imap_mailbox:
		po_imap_mailbox = legacy_imap_mailbox

	return Config(
		company_id=(env("COMPANY_ID", env("ACCOUNTING_COMPANY_ID")) or "").strip() or None,
		use_supabase_routes=_parse_bool(env("USE_SUPABASE_ROUTES", "false"), False),
		supabase_url=env("SUPABASE_URL", "").strip(),
		supabase_service_role_key=env("SUPABASE_SERVICE_ROLE_KEY", "").strip(),
		supabase_timeout_seconds=_parse_int(env("SUPABASE_TIMEOUT_SECONDS", "10"), 10),
		recipient_header_priority=_parse_list(
			env(
				"RECIPIENT_HEADER_PRIORITY",
				"x-original-to,delivered-to,envelope-to,x-envelope-to,to"
			)
		),
		po_webhook_url=po_webhook_url,
		accounting_webhook_url=accounting_webhook_url,
		accounting_automation_secret=env("ACCOUNTING_AUTOMATION_SECRET", "").strip(),
		imap_host=env("IMAP_HOST", "127.0.0.1").strip(),
		imap_port=_parse_int(env("IMAP_PORT", "993"), 993),
		imap_tls=_parse_bool(env("IMAP_TLS", "true"), True),
		imap_tls_verify=_parse_bool(env("IMAP_TLS_VERIFY", "false"), False),
		imap_username=legacy_imap_username,
		imap_password=legacy_imap_password,
		imap_mailbox=legacy_imap_mailbox,
		accounting_imap_username=accounting_imap_username,
		accounting_imap_password=accounting_imap_password,
		accounting_imap_mailbox=accounting_imap_mailbox,
		po_imap_username=po_imap_username,
		po_imap_password=po_imap_password,
		po_imap_mailbox=po_imap_mailbox,
		processed_mailbox=env("PROCESSED_MAILBOX", "INBOX.Archive.Processed").strip(),
		failed_mailbox=env("FAILED_MAILBOX", "INBOX.Archive.Failed").strip(),
		quarantine_mailbox=env("QUARANTINE_MAILBOX", "INBOX.Archive.Quarantine")
		.strip()
		or None,
		accounting_allowed_mime_types=_parse_list(
			env(
				"ACCOUNTING_ALLOWED_MIME_TYPES",
				env("ALLOWED_MIME_TYPES", "application/pdf,image/jpeg,image/png,image/heic,text/csv,application/csv")
			)
		),
		po_allowed_mime_types=_parse_list(
			env(
				"PO_ALLOWED_MIME_TYPES",
				"application/pdf,image/jpeg,image/png,image/webp,image/heic,image/heif,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain,text/csv,application/csv"
			)
		),
		max_attachment_bytes=_parse_int(env("MAX_ATTACHMENT_BYTES", "26214400"), 26214400),
		max_body_chars=_parse_int(env("MAX_BODY_CHARS", "20000"), 20000),
		poll_limit=_parse_int(env("POLL_LIMIT", "25"), 25),
		retry_failed=_parse_bool(env("RETRY_FAILED", "true"), True),
		dry_run=_parse_bool(env("DRY_RUN", "false"), False),
		process_all=_parse_bool(env("PROCESS_ALL", "false"), False)
	)
