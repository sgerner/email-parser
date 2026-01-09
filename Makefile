.PHONY: install uninstall run-once status logs

install:
	@sudo ./install/install.sh --apply

uninstall:
	@sudo ./install/uninstall.sh --apply

run-once:
	@/usr/bin/python3 -m src.ingest --run-once

status:
	@systemctl status accounting-ingest.timer --no-pager

logs:
	@journalctl -u accounting-ingest.service -f
