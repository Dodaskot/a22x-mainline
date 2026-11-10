#!/usr/bin/env python3
"""UI contract test double only. No IPC, gadget, mount, device or MTP operations."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["fixture"], required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("operation", choices=["status", "start", "stop"])
    parser.add_argument("--volume")
    parser.add_argument("--expected-volume-token")
    parser.add_argument("--expected-mount-token")
    parser.add_argument("--read-only", action="store_true")
    parser.add_argument("--expected-session-token")
    args = parser.parse_args()
    root = args.fixture.resolve(strict=True)
    config = json.loads((root / "usb-fixture.json").read_text())
    state_path = root / "usb-fixture-state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else dict(state="stopped", session_token=None, exports=[])
    with (root / "usb-requests.jsonl").open("a") as log:
        log.write(json.dumps(vars(args), default=str) + "\n")

    def snapshot():
        return dict(ok=True, operation=args.operation, mode="fixture", available=config.get("available", True),
                    reason=config.get("reason"), candidates=config["candidates"] if state["state"] == "stopped" else [], **state)

    def error(code):
        print(json.dumps(dict(ok=False, mode="fixture", operation=args.operation,
                              error=dict(code=code, message="UI contract fixture refused " + code))))
        return 1

    if args.operation == "start":
        if not config.get("available", True):
            return error("USB_RUNTIME_UNAVAILABLE")
        if state["state"] != "stopped":
            return error("USB_EXPORT_ACTIVE")
        candidate = next((c for c in config["candidates"] if c["id"] == args.volume), None)
        if not candidate or candidate["volume_token"] != args.expected_volume_token:
            return error("VOLUME_CHANGED")
        if candidate["mount_token"] != args.expected_mount_token:
            return error("MOUNT_CHANGED")
        if config.get("behavior") != "not-applied":
            state = dict(state="active", session_token=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
                         exports=[dict(label=candidate["id"], volume_token=candidate["volume_token"],
                                       mount_token=candidate["mount_token"], access="ro" if args.read_only else candidate["actual_access"],
                                       new_uid=0, new_gid=0)])
            state_path.write_text(json.dumps(state))
    elif args.operation == "stop":
        if not state["session_token"] or state["session_token"] != args.expected_session_token:
            return error("SESSION_CHANGED")
        state = dict(state="stopped", session_token=None, exports=[])
        state_path.write_text(json.dumps(state))
    print(json.dumps(snapshot()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
