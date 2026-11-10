#!/usr/bin/env python3
"""Deterministic UI-only display fixture. Never opens device nodes or DRM."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time


def light_status(state):
    if not state.get("fixture_light", True):
        return dict(brightness_supported=False, blank_supported=False, brightness_percent=None,
                    brightness_raw=None, brightness_max=None, brightness_requested_raw=None,
                    brightness_actual_raw=None, blanked=None, last_nonzero_percent=None)
    maximum = state.get("brightness_max", 255)
    raw = state.get("brightness_raw", 163)
    last = state.get("last_nonzero_raw", raw or 163)
    percent = lambda value: max(1, (value * 100 + maximum // 2) // maximum) if value else 0
    return dict(brightness_supported=True, blank_supported=True, brightness_percent=percent(raw),
                brightness_raw=raw, brightness_max=maximum, brightness_requested_raw=raw,
                brightness_actual_raw=raw, blanked=raw == 0, last_nonzero_percent=percent(last))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("operation", choices=("status", "set", "brightness", "blank", "wake", "toggle"))
    parser.add_argument("--hz", type=int, choices=(60, 90))
    parser.add_argument("--percent", type=int, choices=range(1, 101))
    args = parser.parse_args()
    path = args.fixture / "display-fixture.json"
    state = json.loads(path.read_text())
    sentinel = os.environ.get("A22X_DISPLAY_FIXTURE_SENTINEL_FD")
    leaked_fd = False
    if sentinel is not None:
        try:
            os.fstat(int(sentinel))
            leaked_fd = True
        except OSError:
            pass
    with (args.fixture / "display-requests.jsonl").open("a") as log:
        log.write(json.dumps({"operation": args.operation, "hz": args.hz, "percent": args.percent,
                              "lease_env": "A22X_RECOVERY_LOCK_FD" in os.environ,
                              "sentinel_fd_inherited": leaked_fd}) + "\n")
    if args.operation == "set":
        if state.get("behavior") == "set-error":
            print(json.dumps({"ok": False, "error": {"code": "FIXTURE_SET_FAILED", "message": "Fixture rejected mode change."}}))
            return 1
        if args.hz not in state["available_hz"] or not state["service_running"]:
            raise AssertionError("UI attempted an unavailable mode")
        state["current_hz"] = args.hz
        state["actual_hz"] = 59.9998 if args.hz == 60 else 89.9999
        # A deliberately misleading set result must fail the UI's separate status check.
        if state.get("behavior") != "not-applied":
            path.write_text(json.dumps(state))
    elif args.operation in ("brightness", "blank", "wake", "toggle"):
        if not state.get("fixture_light", True):
            print(json.dumps({"ok": False, "operation": args.operation,
                              "error": {"code": "UNSUPPORTED", "message": "No fixture backlight"}}))
            return 2
        original = dict(state)
        raw = state.get("brightness_raw", 163)
        maximum = state.get("brightness_max", 255)
        last = state.get("last_nonzero_raw", raw or 163)
        if args.operation == "brightness":
            assert args.percent is not None
            raw = max(1, (args.percent * maximum + 50) // 100)
        elif args.operation == "blank" or (args.operation == "toggle" and raw > 0):
            if raw:
                last = raw
            raw = 0
        else:
            raw = raw or last
        state["brightness_raw"] = raw
        state["last_nonzero_raw"] = raw or last
        state["applied_controls"] = state.get("applied_controls", 0) + 1
        fault = state.get("control_behavior")
        first = not state.get("control_fault_consumed", False)
        if first:
            state["control_fault_consumed"] = True
        if fault == "brightness-not-applied":
            original["control_fault_consumed"] = True
            path.write_text(json.dumps(original))
        else:
            path.write_text(json.dumps(state))
        if first and fault == "lost-reply":
            print("fixture reply was lost")
            return 2
        if first and fault == "timeout-reply":
            time.sleep(8)  # UI owns and bounds this child; the state was already applied.
    if args.operation == "status" and state.get("control_behavior") == "lost-status" and state.get("control_fault_consumed") and not state.get("status_fault_consumed"):
        state["status_fault_consumed"] = True
        path.write_text(json.dumps(state))
        print("fixture status was lost")
        return 2
    result = {"ok": True, "operation": args.operation, "boot_id": "fixture-display-boot", **state, **light_status(state)}
    if state.get("control_behavior") == "malformed-status":
        result["blanked"] = "false"
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
