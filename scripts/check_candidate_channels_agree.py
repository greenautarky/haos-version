#!/usr/bin/env python3
"""On a candidate/* branch, the three channel files must offer the SAME versions.

Why (2026-10-08): a canary on Supervisor channel `dev` polled
candidate/stable-1.5/dev.json, which still offered Supervisor 2025.11.5.7 while
stable.json offered 2025.11.5.9 — the release's own image. The convergence
stopped at the Supervisor step. Two files claiming the same truth must be
compared (rule 61), so on a candidate branch every key except `channel` must be
identical across stable/beta/dev.

Usage: check_candidate_channels_agree.py [DIR]   (default: repo root)
Exit 0 = agree (or not a candidate branch), 1 = divergence, 2 = file missing/unreadable.
"""
import json, os, subprocess, sys

FILES = ("stable.json", "beta.json", "dev.json")
IGNORED = {"channel"}


def flat(d, p=""):
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out.update(flat(v, p + k + "."))
        else:
            out[p + k] = v
    return out


def compare(root):
    data = {}
    for f in FILES:
        path = os.path.join(root, f)
        try:
            with open(path) as fh:
                data[f] = flat(json.load(fh))
        except (OSError, ValueError) as e:
            print(f"FAIL: cannot read {path}: {e}")
            return 2
    keys = sorted(set().union(*data.values()) - IGNORED)
    bad = []
    for k in keys:
        vals = {f: data[f].get(k) for f in FILES}
        if len(set(map(json.dumps, vals.values()))) > 1:
            bad.append((k, vals))
    for k, vals in bad:
        print(f"FAIL: {k} differs: " + ", ".join(f"{f}={v}" for f, v in vals.items()))
    if bad:
        return 1
    print(f"OK CANDIDATE-CHANNELS: {len(keys)} keys identical across {', '.join(FILES)}")
    return 0


def branch():
    ref = os.environ.get("GITHUB_HEAD_REF") or os.environ.get("GITHUB_REF_NAME")
    if ref:
        return ref
    try:
        return subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    if len(sys.argv) == 1 and not branch().startswith("candidate/") and not os.environ.get("FORCE_CANDIDATE_CHECK"):
        # A PR INTO a candidate branch is checked too (base ref).
        if not os.environ.get("GITHUB_BASE_REF", "").startswith("candidate/"):
            print(f"SKIP: branch '{branch()}' is not a candidate/* branch")
            sys.exit(0)
    sys.exit(compare(root))
