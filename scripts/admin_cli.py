#!/usr/bin/env python3
"""PEAR administrative CLI (v2.40)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import Config, get_config, set_config
from core.backup import BackupManager
from core.ops import diagnostics, integrity_check, resource_usage
from core.audit import AuditLog
from core.logging_util import setup_logging


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pear-admin", description="PEAR operations admin")
    parser.add_argument("--profile", default=None, help="config profile")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("config", help="show configuration")
    p_backup = sub.add_parser("backup", help="create backup")
    p_backup.add_argument("--label", default="manual")
    p_restore = sub.add_parser("restore", help="restore backup")
    p_restore.add_argument("path")
    p_restore.add_argument("--dry-run", action="store_true")
    sub.add_parser("list-backups")
    p_verify = sub.add_parser("verify-backup")
    p_verify.add_argument("path")
    sub.add_parser("diagnostics")
    sub.add_parser("integrity")
    sub.add_parser("resources")
    p_audit = sub.add_parser("audit")
    p_audit.add_argument("--limit", type=int, default=20)

    args = parser.parse_args(argv)
    cfg = Config(profile=args.profile) if args.profile else get_config()
    set_config(cfg)
    setup_logging(cfg.get("log_level", "INFO"), json_mode=bool(cfg.get("log_json")))

    data_dir = Path(str(cfg.get("data_dir")))
    backups = BackupManager(data_dir, backup_dir=Path(str(cfg.get("backup_dir"))))

    if args.cmd == "config":
        print(json.dumps(cfg.as_dict(), indent=2))
    elif args.cmd == "backup":
        print(json.dumps(backups.create(label=args.label), indent=2))
    elif args.cmd == "list-backups":
        print(json.dumps(backups.list_backups(), indent=2))
    elif args.cmd == "verify-backup":
        print(json.dumps(backups.verify(Path(args.path)), indent=2))
    elif args.cmd == "restore":
        print(json.dumps(backups.restore(Path(args.path), dry_run=args.dry_run), indent=2))
    elif args.cmd == "diagnostics":
        print(json.dumps(diagnostics(None), indent=2))
    elif args.cmd == "integrity":
        print(json.dumps(integrity_check(data_dir), indent=2))
    elif args.cmd == "resources":
        print(json.dumps(resource_usage(), indent=2))
    elif args.cmd == "audit":
        log = AuditLog(path=data_dir / "audit.jsonl")
        print(json.dumps(log.read_file(limit=args.limit), indent=2))
    else:
        parser.error("unknown command")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
