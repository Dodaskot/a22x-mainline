#!/usr/bin/env python3
"""Host-only Linux v2 image acceptance and qualification-capsule construction.

Neither command deploys a capsule or accepts a block target. The images command
creates an exclusively new directory containing only new regular target images.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
import sys

from common import RecoveryError, no_symlinks, read_json, write_json_new
from linux_workflow import qualify_to_images
from qualification import make_capsule
from recoveryctl import Parser, progress


def main(argv=None):
    try:
        parser = Parser(description=__doc__)
        commands = parser.add_subparsers(dest="command", required=True, parser_class=Parser)
        images = commands.add_parser("images")
        images.add_argument("--backup", required=True)
        images.add_argument("--output-directory", required=True)
        images.add_argument("--implementation", required=True)
        images.add_argument("--toolchain", required=True)
        capsule = commands.add_parser("capsule")
        capsule.add_argument("--implementation", required=True)
        capsule.add_argument("--acceptance", required=True)
        capsule.add_argument("--output", required=True)
        args = parser.parse_args(argv)
        record = read_json(no_symlinks(Path(args.implementation)))
        if record.get("operation") == "qualification-record":
            record = record["implementation"]
        if args.command == "images":
            result = qualify_to_images(args.backup, args.output_directory, record,
                                       read_json(no_symlinks(Path(args.toolchain))), progress)
        else:
            value = make_capsule(record, read_json(no_symlinks(Path(args.acceptance))))
            path = no_symlinks(Path(args.output), must_exist=False)
            write_json_new(path, value)
            result = {"ok": True, "operation": "capsule", "capsule_id": value["capsule_id"],
                      "output": str(path), "deployed": False, "physical_device_written": False}
        print(json.dumps(result, separators=(",", ":")), flush=True)
        return 0
    except RecoveryError as exc:
        result = {"ok": False, "error": {"code": exc.code, "message": exc.message, "details": exc.details}}
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        result = {"ok": False, "error": {"code": "QUALIFICATION_FAILED", "message": str(exc)}}
    print(json.dumps(result, separators=(",", ":")), flush=True)
    return 2


if __name__ == "__main__":
    sys.exit(main())
