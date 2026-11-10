#!/usr/bin/env python3
"""Create tiny, explicitly synthetic partition files; never opens /dev nodes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import no_symlinks, write_json_new


def create_fixture(root: Path, userdata_bytes=4 * 1024 * 1024):
    root = no_symlinks(root, must_exist=False)
    if not isinstance(userdata_bytes, int) or not 1024 <= userdata_bytes <= 64 * 1024 * 1024:
        raise ValueError("Demo userdata size must be 1 KiB through 64 MiB")
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    (root / "partitions").mkdir()
    (root / "storage/sd").mkdir(parents=True)
    (root / "plans").mkdir()
    sizes = {"boot": 64 * 1024, "recovery": 64 * 1024,
             "metadata": 32 * 1024, "userdata": userdata_bytes, "super": 128 * 1024,
             "tee1": 16 * 1024, "tzar": 16 * 1024, "dtbo": 16 * 1024, "vbmeta": 4096}
    numbers = {"boot": 41, "recovery": 42, "metadata": 31, "userdata": 54,
               "super": 43, "tee1": 14, "tzar": 15, "dtbo": 18, "vbmeta": 21}
    nodes = {"179:0": {"parent": None, "slaves": []},
             "8:0": {"parent": None, "slaves": []},
             "8:1": {"parent": "8:0", "slaves": []},
             "253:0": {"parent": None, "slaves": ["179:54"]}}
    partitions, retained = {}, {}
    for label, size in sizes.items():
        path = root / "partitions" / (label + ".img")
        # Sparse extent plus identifying bytes: this is synthetic test data.
        with path.open("xb") as stream:
            stream.truncate(size)
            stream.write(("A22X FIXTURE " + label + "\n").encode())
            stream.seek(size - 32)
            stream.write((label.encode() + b"_END").ljust(32, b"!"))
        number = numbers[label]
        device = "179:" + str(number)
        nodes[device] = {"parent": "179:0", "slaves": []}
        entry = {"path": "partitions/" + label + ".img", "size_bytes": size, "device": device}
        (partitions if label in {"boot", "recovery", "metadata", "userdata"} else retained)[label] = entry
    config = {"schema": "a22x.fixture.v1", "device": {"model": "SM-A226B (synthetic fixture)",
              "identity": "fixture:a22x-demo-01"}, "partitions": partitions,
              "retained_partitions": retained, "firmware": {"fingerprint": "fixture/android-baseline-v1"},
              "storage_graph": nodes, "mounts": [],
              "repositories": [{"path": "storage/sd", "device": "8:1", "filesystem": "ext4", "read_only": False}]}
    write_json_new(root / "fixture.json", config)
    return {"fixture_root": str(root), "repository": str(root / "storage/sd"),
            "plans": str(root / "plans"), "synthetic": True, "physical_device_written": False}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("root", type=Path)
    p.add_argument("--userdata-bytes", type=int, default=4 * 1024 * 1024)
    args = p.parse_args()
    print(json.dumps(create_fixture(args.root, args.userdata_bytes), indent=2))
