#!/usr/bin/env bash
set -euo pipefail

MODE=""
RUN_SMOKE_TEST="ask"
SMOKE_TEST_RECIPIENT=""

usage() {
	cat <<'EOF'
Usage:
  ./install/install.sh --apply [--smoke-test] [--smoke-test-recipient <email>] [--no-smoke-test]
  ./install/install.sh --dry-run [--smoke-test] [--smoke-test-recipient <email>]
EOF
}

while [[ $# -gt 0 ]]; do
	case "$1" in
		--dry-run)
			MODE="dry"
			shift
			;;
		--apply)
			MODE="apply"
			shift
			;;
		--smoke-test)
			RUN_SMOKE_TEST="true"
			shift
			;;
		--no-smoke-test)
			RUN_SMOKE_TEST="false"
			shift
			;;
		--smoke-test-recipient)
			shift
			if [[ $# -eq 0 ]]; then
				echo "Missing value for --smoke-test-recipient."
				usage
				exit 1
			fi
			SMOKE_TEST_RECIPIENT="$1"
			RUN_SMOKE_TEST="true"
			shift
			;;
		--smoke-test-recipient=*)
			SMOKE_TEST_RECIPIENT="${1#*=}"
			RUN_SMOKE_TEST="true"
			shift
			;;
		*)
			echo "Unknown argument: $1"
			usage
			exit 1
			;;
	esac
done

if [[ -z "$MODE" ]]; then
	usage
	exit 1
fi

if [[ "$MODE" == "apply" && "$EUID" -ne 0 ]]; then
	echo "Run with sudo for --apply."
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

info() {
	echo "[info] $*"
}

fail() {
	echo "[error] $*" >&2
	exit 1
}

require_cmd() {
	local cmd="$1"
	local hint="$2"
	if ! command -v "$cmd" >/dev/null 2>&1; then
		fail "Missing required command '$cmd'. ${hint}"
	fi
}

append_master_cf_pipe_if_missing() {
	local master_cf="$1"
	if [[ ! -f "$master_cf" ]]; then
		fail "Postfix master config not found at ${master_cf}. Is postfix installed correctly?"
	fi

	if grep -qE '^farin_ingest[[:space:]]+unix' "$master_cf"; then
		info "Postfix master.cf already has farin_ingest transport."
		return
	fi

	run_cmd "cat <<'EOF' >> ${master_cf}

farin_ingest unix  -       n       n       -       -       pipe
  flags=Rq user=accounting-ingest argv=/usr/bin/python3 /opt/accounting-ingest/src/postfix_pipe.py --config /etc/accounting-ingest/accounting-ingest.env --recipient \${original_recipient} --unknown-recipient-action bounce
EOF"
}

configure_postfix() {
	local transport_map="$1"
	local master_cf="$2"

	require_cmd postconf "Install postfix (for Debian/Ubuntu: apt install postfix)."
	require_cmd postfix "Install postfix (for Debian/Ubuntu: apt install postfix)."
	require_cmd systemctl "This installer expects systemd to reload postfix."

	if [[ ! -d /etc/postfix ]]; then
		fail "/etc/postfix is missing. Postfix configuration directory is required."
	fi

	run_cmd "cat <<'EOF' > ${transport_map}
/^[a-z0-9-]+-(po|accounting)@farin\\.app$/    farin_ingest:
EOF"
	run_cmd "chmod 644 ${transport_map}"

	append_master_cf_pipe_if_missing "$master_cf"

	local new_map="regexp:${transport_map}"
	local current_maps
	current_maps="$(postconf -h transport_maps || true)"

	if [[ "$MODE" == "dry" ]]; then
		if [[ -z "$current_maps" ]]; then
			echo "[dry-run] postconf -e \"transport_maps = ${new_map}\""
		elif [[ "$current_maps" == *"$new_map"* ]]; then
			echo "[dry-run] transport_maps already contains ${new_map}"
		else
			echo "[dry-run] postconf -e \"transport_maps = ${current_maps}, ${new_map}\""
		fi
		echo "[dry-run] postconf -e \"farin_ingest_destination_recipient_limit = 1\""
		echo "[dry-run] postfix check"
		echo "[dry-run] systemctl reload postfix"
		return
	fi

	if [[ -z "$current_maps" ]]; then
		postconf -e "transport_maps = ${new_map}" || fail "Unable to set transport_maps in postconf."
	elif [[ "$current_maps" != *"$new_map"* ]]; then
		postconf -e "transport_maps = ${current_maps}, ${new_map}" || fail "Unable to append ${new_map} to transport_maps."
	else
		info "transport_maps already contains ${new_map}"
	fi

	postconf -e "farin_ingest_destination_recipient_limit = 1" || fail "Unable to set farin_ingest_destination_recipient_limit."

	if ! postfix check; then
		fail "postfix check failed. Validate /etc/postfix/master.cf and ${transport_map}."
	fi
	if ! systemctl reload postfix; then
		fail "Failed to reload postfix. Inspect: journalctl -u postfix -n 200 --no-pager"
	fi

	info "Postfix far-in ingest transport configured and reloaded."
}

get_env_value() {
	local key="$1"
	local env_file="$2"
	if [[ ! -f "$env_file" ]]; then
		return
	fi
	awk -F= -v k="$key" '$1 == k {print $2; exit}' "$env_file" | tr -d '[:space:]'
}

run_smoke_test() {
	local install_dir="$1"
	local env_file="$2"
	local recipient="$SMOKE_TEST_RECIPIENT"

	if [[ -z "$recipient" ]]; then
		recipient="$(get_env_value "SMOKE_TEST_RECIPIENT" "$env_file")"
	fi

	if [[ -z "$recipient" && -t 0 ]]; then
		read -r -p "Smoke test recipient (e.g. buttered-up-po@farin.app, blank to skip): " recipient
	fi

	if [[ -z "$recipient" ]]; then
		info "Skipping smoke test: no recipient provided."
		echo "Smoke test command to run later:"
		echo "  /usr/bin/python3 ${install_dir}/src/postfix_pipe.py --config ${env_file} --recipient <recipient> --dry-run --unknown-recipient-action tempfail"
		return
	fi

	local cmd="/usr/bin/python3 ${install_dir}/src/postfix_pipe.py --config ${env_file} --recipient ${recipient} --dry-run --unknown-recipient-action tempfail --log-level INFO"
	if [[ "$MODE" == "dry" ]]; then
		echo "[dry-run] ${cmd} <<'EOF'"
		echo "[dry-run] From: smoke-test@localhost"
		echo "[dry-run] To: ${recipient}"
		echo "[dry-run] Subject: Postfix Pipe Smoke Test"
		echo "[dry-run]"
		echo "[dry-run] This is a dry-run smoke test from install.sh."
		echo "[dry-run] EOF"
		return
	fi

	info "Running smoke test for recipient ${recipient}"
	if ! /usr/bin/python3 "${install_dir}/src/postfix_pipe.py" \
		--config "${env_file}" \
		--recipient "${recipient}" \
		--dry-run \
		--unknown-recipient-action tempfail \
		--log-level INFO <<EOF
From: smoke-test@localhost
To: ${recipient}
Subject: Postfix Pipe Smoke Test

This is a dry-run smoke test from install.sh.
EOF
	then
		fail "Smoke test failed. Check ${env_file} values and logs."
	fi

	info "Smoke test completed successfully."
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
INSTALL_DIR="/opt/accounting-ingest"
CONFIG_DIR="/etc/accounting-ingest"
ENV_FILE="${CONFIG_DIR}/accounting-ingest.env"
SERVICE_SRC="${ROOT_DIR}/systemd/accounting-ingest.service"
TIMER_SRC="${ROOT_DIR}/systemd/accounting-ingest.timer"
POSTFIX_MASTER_CF="/etc/postfix/master.cf"
POSTFIX_TRANSPORT_MAP="/etc/postfix/transport_farin.regexp"

run_cmd "install -d -m 755 ${INSTALL_DIR}"
run_cmd "install -d -m 755 ${CONFIG_DIR}"

if ! id accounting-ingest >/dev/null 2>&1; then
	run_cmd "useradd --system --no-create-home --shell /usr/sbin/nologin accounting-ingest"
fi

if [[ ! -f "$ENV_FILE" ]]; then
	run_cmd "cat <<'EOF' > ${ENV_FILE}
# Accounting ingest service configuration
# Dynamic multi-tenant routing (recommended for farin.app aliases)
USE_SUPABASE_ROUTES=false
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
SUPABASE_TIMEOUT_SECONDS=10
RECIPIENT_HEADER_PRIORITY=x-original-to,delivered-to,envelope-to,x-envelope-to,to

# Optional fixed fallback for legacy single-company mode
COMPANY_ID=

# Optional default recipient for install smoke test
SMOKE_TEST_RECIPIENT=

PO_WEBHOOK_URL=https://<our-domain>/api/v1/customers/wholesale/orders/po-drafts
ACCOUNTING_WEBHOOK_URL=https://<our-domain>/api/v1/accounting/automation/ingest
ACCOUNTING_AUTOMATION_SECRET=
IMAP_HOST=127.0.0.1
IMAP_PORT=993
IMAP_TLS=true
IMAP_TLS_VERIFY=false

# Dynamic mode mailbox credentials (single inbound mailbox; only needed for IMAP polling mode)
IMAP_USERNAME=
IMAP_PASSWORD=
IMAP_MAILBOX=INBOX

# Legacy static mode mailboxes
ACCOUNTING_IMAP_USERNAME=accounting@butteredupbakery.com
ACCOUNTING_IMAP_PASSWORD=
ACCOUNTING_IMAP_MAILBOX=INBOX
PO_IMAP_USERNAME=po@butteredupbakery.com
PO_IMAP_PASSWORD=
PO_IMAP_MAILBOX=INBOX

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
fi

run_cmd "chmod 600 ${ENV_FILE}"
run_cmd "chown accounting-ingest:accounting-ingest ${ENV_FILE}"

if command -v rsync >/dev/null 2>&1; then
	run_cmd "rsync -a --delete --exclude '.git' --exclude '__pycache__' ${ROOT_DIR}/ ${INSTALL_DIR}/"
else
	run_cmd "install -d -m 755 ${INSTALL_DIR}/src ${INSTALL_DIR}/systemd"
	run_cmd "cp -a ${ROOT_DIR}/src/. ${INSTALL_DIR}/src/"
	run_cmd "cp -a ${ROOT_DIR}/systemd/. ${INSTALL_DIR}/systemd/"
	if [[ -f ${ROOT_DIR}/README.md ]]; then
		run_cmd "cp -a ${ROOT_DIR}/README.md ${INSTALL_DIR}/README.md"
	fi
	if [[ -f ${ROOT_DIR}/Makefile ]]; then
		run_cmd "cp -a ${ROOT_DIR}/Makefile ${INSTALL_DIR}/Makefile"
	fi
fi

run_cmd "chmod 755 ${INSTALL_DIR}/src/postfix_pipe.py"
run_cmd "chown -R accounting-ingest:accounting-ingest ${INSTALL_DIR}"

run_cmd "install -m 644 ${SERVICE_SRC} /etc/systemd/system/accounting-ingest.service"
run_cmd "install -m 644 ${TIMER_SRC} /etc/systemd/system/accounting-ingest.timer"
run_cmd "systemctl daemon-reload"

configure_postfix "${POSTFIX_TRANSPORT_MAP}" "${POSTFIX_MASTER_CF}"

if [[ "$RUN_SMOKE_TEST" == "true" ]]; then
	run_smoke_test "${INSTALL_DIR}" "${ENV_FILE}"
elif [[ "$RUN_SMOKE_TEST" == "ask" && "$MODE" == "apply" && -t 0 ]]; then
	read -r -p "Run postfix pipe smoke test now? [y/N]: " run_choice
	if [[ "$run_choice" =~ ^[Yy]$ ]]; then
		run_smoke_test "${INSTALL_DIR}" "${ENV_FILE}"
	fi
fi

echo
echo "Install complete."
echo
echo "Next steps:"
echo "1) Edit ${ENV_FILE} with webhook credentials and (if dynamic mode) Supabase credentials."
echo "2) Validate postfix routing:"
echo "   postconf -n | grep -E '^(transport_maps|farin_ingest_destination_recipient_limit)='"
echo "   postconf -M farin_ingest/unix"
echo "3) If still using IMAP polling mode, enable timer:"
echo "   sudo systemctl enable --now accounting-ingest.timer"
echo "4) Logs:"
echo "   journalctl -u accounting-ingest.service -f"
