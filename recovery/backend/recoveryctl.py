#!/usr/bin/env python3
"""JSON command line for the A22x RAM recovery prototype."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

from common import RecoveryError
from engine import (backup, inventory, list_backups, load_plan, make_backup_plan, manage_backup,
                    make_restore_plan, make_wipe_plan, restore, save_plan, verify_backup, wipe)
from storage import DeviceContext, FixtureContext


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise RecoveryError("INVALID_ARGUMENT", message)


def parser():
    p = Parser(description=__doc__)
    choice = p.add_mutually_exclusive_group(required=True)
    choice.add_argument("--fixture-root", help="Explicit directory of regular-file test partitions")
    choice.add_argument("--device", action="store_true", help="Production adapter; only separately qualified Linux v2 may write BOOT/userdata")
    p.add_argument("--device-config", default="/etc/a22x-recovery/device.json")
    commands = p.add_subparsers(dest="command", required=True, parser_class=Parser)
    commands.add_parser("inventory")
    commands.add_parser("qualification-record")
    command = commands.add_parser("list")
    command.add_argument("--repository", required=True)
    command = commands.add_parser("backup-plan")
    command.add_argument("--profile", required=True, choices=["linux", "android", "recovery"])
    command.add_argument("--repository", required=True)
    command.add_argument("--name", required=True)
    command.add_argument("--output", required=True)
    command.add_argument("--compression", choices=["auto", "gzip", "zstd"], default="auto")
    command.add_argument("--format", dest="data_format", choices=["raw", "partclone-ext4"], default="raw")
    for operation in ("restore-plan", "wipe-plan"):
        command = commands.add_parser(operation)
        command.add_argument("--backup", required=True)
        command.add_argument("--output", required=True)
    command = commands.add_parser("verify")
    command.add_argument("--backup", required=True)
    command = commands.add_parser("rename-backup")
    command.add_argument("--backup", required=True)
    command.add_argument("--name", required=True)
    command = commands.add_parser("delete-backup")
    command.add_argument("--backup", required=True)
    command.add_argument("--confirm", required=True)
    for operation in ("backup", "restore", "wipe"):
        command = commands.add_parser(operation)
        command.add_argument("--plan", required=True)
        command.add_argument("--confirm", required=True)
    return p


_last_progress = (None, 0.0)


def progress(event):
    global _last_progress
    key = (event.get("operation"), event.get("phase"), event.get("partition"))
    now = time.monotonic()
    final = event.get("bytes_done") == event.get("bytes_total")
    if key == _last_progress[0] and now - _last_progress[1] < 0.2 and not final:
        return
    _last_progress = (key, now)
    print(json.dumps(event, separators=(",", ":"), ensure_ascii=True), file=sys.stderr, flush=True)


def main(argv=None):
    operation = "unknown"
    try:
        args = parser().parse_args(argv)
        operation = args.command
        context = FixtureContext(args.fixture_root) if args.fixture_root else DeviceContext(args.device_config)
        if operation == "inventory":
            result = inventory(context)
        elif operation == "qualification-record":
            from qualification import implementation
            result = {"ok": True, "operation": operation, "implementation": implementation(context)}
        elif operation == "list":
            result = list_backups(context, args.repository)
        elif operation == "backup-plan":
            plan = make_backup_plan(context, args.profile, args.repository, args.name, args.compression, progress,
                                    data_format=args.data_format)
            result = save_plan(context, plan, args.output)
        elif operation == "restore-plan":
            plan = make_restore_plan(context, args.backup, progress)
            result = save_plan(context, plan, args.output)
        elif operation == "wipe-plan":
            plan = make_wipe_plan(context, args.backup, progress)
            result = save_plan(context, plan, args.output)
        elif operation == "verify":
            result = verify_backup(context, args.backup, progress)
            result.pop("archive_stamps", None)
        elif operation == "rename-backup":
            result = manage_backup(context, args.backup, "rename", new_name=args.name)
        elif operation == "delete-backup":
            result = manage_backup(context, args.backup, "delete", confirm=args.confirm)
        elif operation == "backup":
            result = backup(context, load_plan(context, args.plan), args.confirm, progress)
        elif operation == "wipe":
            result = wipe(context, load_plan(context, args.plan), args.confirm, progress)
        else:
            result = restore(context, load_plan(context, args.plan), args.confirm, progress)
        print(json.dumps(result, separators=(",", ":"), ensure_ascii=True), flush=True)
        return 0
    except RecoveryError as exc:
        result = {"ok": False, "operation": operation,
                  "error": {"code": exc.code, "message": exc.message, "details": exc.details}}
        code = 4 if exc.code == "DEVICE_RESTORE_NOT_QUALIFIED" else 2
    except KeyboardInterrupt as exc:
        details = {"auto_reboot": False}
        if hasattr(exc, "restore_receipt"):
            details["receipt"] = exc.restore_receipt
            details["partial_restore_possible"] = exc.restore_receipt["partial_restore_possible"]
        result = {"ok": False, "operation": operation,
                  "error": {"code": "INTERRUPTED", "message": "Operation interrupted; no automatic reboot",
                            "details": details}}
        code = 130
    except OSError as exc:
        details = {"errno": exc.errno, "auto_reboot": False}
        if hasattr(exc, "restore_receipt"):
            details["receipt"] = exc.restore_receipt
            details["partial_restore_possible"] = exc.restore_receipt["partial_restore_possible"]
        result = {"ok": False, "operation": operation,
                  "error": {"code": "IO_ERROR", "message": str(exc),
                            "details": details}}
        code = 3
    except subprocess.SubprocessError as exc:
        details = {"auto_reboot": False}
        if hasattr(exc, "restore_receipt"):
            details["receipt"] = exc.restore_receipt
            details["partial_restore_possible"] = exc.restore_receipt["partial_restore_possible"]
        result = {"ok": False, "operation": operation,
                  "error": {"code": "TOOL_PROCESS_FAILED", "message": str(exc), "details": details}}
        code = 3
    except (KeyError, TypeError, ValueError) as exc:
        result = {"ok": False, "operation": operation,
                  "error": {"code": "INVALID_INPUT", "message": str(exc), "details": {}}}
        code = 2
    print(json.dumps(result, separators=(",", ":"), ensure_ascii=True), flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
