#!/usr/bin/env bash
set -euo pipefail

MODE=""
for arg in "$@"; do
	case "$arg" in
		--dry-run)
			MODE="dry"
			;;
		--apply)
			MODE="apply"
			;;
	esac
done

if [[ -z "$MODE" ]]; then
	echo "Usage: $0 --dry-run | --apply"
	exit 1
fi

run_cmd() {
	if [[ "$MODE" == "dry" ]]; then
		echo "[dry-run] $*"
	else
		echo "[apply] $*"
		eval "$@"
	fi
}

if [[ "$MODE" == "apply" && "$EUID" -ne 0 ]]; then
	echo "Run with sudo for --apply."
	exit 1
fi

INSTALL_DIR="/opt/accounting-ingest"
CONFIG_DIR="/etc/accounting-ingest"
ENV_FILE="${CONFIG_DIR}/accounting-ingest.env"
SERVICE_SRC="$(dirname "${BASH_SOURCE[0]}")/../systemd/accounting-ingest.service"
TIMER_SRC="$(dirname "${BASH_SOURCE[0]}")/../systemd/accounting-ingest.timer"

run_cmd "install -d -m 755 ${INSTALL_DIR}"
run_cmd "install -d -m 755 ${CONFIG_DIR}"

if ! id accounting-ingest >/dev/null 2>&1; then
	run_cmd "useradd --system --no-create-home --shell /usr/sbin/nologin accounting-ingest"
fi

if [[ ! -f "$ENV_FILE" ]]; then
	run_cmd "cat <<'EOF' > ${ENV_FILE}
# Accounting ingest service configuration
PO_WEBHOOK_URL=https://<our-domain>/api/v1/customers/wholesale/orders/po-drafts
ACCOUNTING_WEBHOOK_URL=https://<our-domain>/api/v1/accounting/automation/ingest
ACCOUNTING_AUTOMATION_SECRET=
IMAP_HOST=127.0.0.1
IMAP_PORT=993
IMAP_TLS=true
IMAP_TLS_VERIFY=false
ACCOUNTING_IMAP_USERNAME=accounting@butteredupbakery.com
ACCOUNTING_IMAP_PASSWORD=
ACCOUNTING_IMAP_MAILBOX=INBOX
PO_IMAP_USERNAME=po@butteredupbakery.com
PO_IMAP_PASSWORD=
PO_IMAP_MAILBOX=INBOX
# Legacy fallback variables (used for accounting route when ACCOUNTING_IMAP_* are unset)
IMAP_USERNAME=
IMAP_PASSWORD=
IMAP_MAILBOX=INBOX
PROCESSED_MAILBOX=INBOX.Archive.Processed
FAILED_MAILBOX=INBOX.Archive.Failed
QUARANTINE_MAILBOX=INBOX.Archive.Quarantine
ACCOUNTING_ALLOWED_MIME_TYPES=application/pdf,image/jpeg,image/png,image/heic,text/csv,application/csv
PO_ALLOWED_MIME_TYPES=application/pdf,image/jpeg,image/png,image/webp,image/heic,image/heif,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain,text/csv,application/csv
MAX_ATTACHMENT_BYTES=26214400
MAX_BODY_CHARS=20000
POLL_LIMIT=25
RETRY_FAILED=true
DRY_RUN=false
PROCESS_ALL=false
EOF"
	run_cmd "chown accounting-ingest:accounting-ingest ${ENV_FILE}"
fi
run_cmd "chmod 600 ${ENV_FILE}"
run_cmd "chown accounting-ingest:accounting-ingest ${ENV_FILE}"

if command -v rsync >/dev/null 2>&1; then
	run_cmd "rsync -a --delete --exclude '.git' --exclude '__pycache__' $(dirname "${BASH_SOURCE[0]}")/../ ${INSTALL_DIR}/"
else
	run_cmd "cp -a $(dirname "${BASH_SOURCE[0]}")/../src ${INSTALL_DIR}/"
	run_cmd "cp -a $(dirname "${BASH_SOURCE[0]}")/../docs ${INSTALL_DIR}/"
	run_cmd "cp -a $(dirname "${BASH_SOURCE[0]}")/../systemd ${INSTALL_DIR}/"
	if [[ -f $(dirname "${BASH_SOURCE[0]}")/../Makefile ]]; then
		run_cmd "cp -a $(dirname "${BASH_SOURCE[0]}")/../Makefile ${INSTALL_DIR}/"
	fi
fi

run_cmd "chown -R accounting-ingest:accounting-ingest ${INSTALL_DIR}"

run_cmd "install -m 644 ${SERVICE_SRC} /etc/systemd/system/accounting-ingest.service"
run_cmd "install -m 644 ${TIMER_SRC} /etc/systemd/system/accounting-ingest.timer"

run_cmd "systemctl daemon-reload"

echo "\nNext steps:"
	echo "1) Edit ${ENV_FILE} with webhook and IMAP credentials."
	echo "2) Enable and start the timer: sudo systemctl enable --now accounting-ingest.timer"
	echo "3) Check logs: journalctl -u accounting-ingest.service -f"
