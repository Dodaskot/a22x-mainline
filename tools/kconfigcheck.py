#!/usr/bin/env python3
"""Check a kernel .config against pmaports kconfigcheck.toml (alias 'community').
usage: kconfigcheck.py kconfigcheck.toml .config KVER ARCH [alias]"""
import re, sys, tomllib

toml_path, cfg_path, kver, arch = sys.argv[1:5]
alias = sys.argv[5] if len(sys.argv) > 5 else "community"
t = tomllib.load(open(toml_path, "rb"))
cfg = {}
for line in open(cfg_path):
    m = re.match(r"^CONFIG_(\w+)=(.*)$", line)
    if m:
        cfg[m.group(1)] = m.group(2).strip('"')

def ver(s):
    return tuple(int(x) for x in re.findall(r"\d+", s.split("_")[0])[:3])

def ver_ok(spec):
    for part in spec.split():
        m = re.match(r"(>=|<=|<|>|==)?(.+)", part)
        op, v = m.group(1) or ">=", ver(m.group(2))
        k = ver(kver)
        if not {">=": k >= v, "<": k < v, "<=": k <= v, ">": k > v, "==": k == v}[op]:
            return False
    return True

missing = []
for cat in t["aliases"][alias]:
    for vspec, arches in t.get(cat, {}).items():
        if not ver_ok(vspec):
            continue
        for archs, opts in arches.items():
            if archs != "all" and arch not in archs.split():
                continue
            for opt, want in opts.items():
                have = cfg.get(opt)
                if want is True or want in ("y", "m"):
                    ok = have == "y" or (want == "m" and have == "m")
                elif want is False or want == "n":
                    ok = have in (None, "n")
                elif isinstance(want, list):
                    ok = have in [str(w) for w in want] or (have == "y" and "y" in [str(w) for w in want])
                else:
                    ok = have == str(want)
                if not ok:
                    missing.append((cat, opt, want, have))

for cat, opt, want, have in missing:
    print(f"{cat:28} CONFIG_{opt}: want {want!r}, have {have!r}")
print(f"{len(missing)} mismatches", file=sys.stderr)
