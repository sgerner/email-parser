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

run_cmd "systemctl disable --now accounting-ingest.timer"
run_cmd "rm -f /etc/systemd/system/accounting-ingest.service"
run_cmd "rm -f /etc/systemd/system/accounting-ingest.timer"
run_cmd "systemctl daemon-reload"

run_cmd "rm -rf /opt/accounting-ingest"

echo "Uninstall completed. Config remains at /etc/accounting-ingest." 
