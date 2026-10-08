#!/usr/bin/env python3
"""No channel file may offer Home Assistant Core 2026.9.0 or newer.

Why this exists: Core 2026.9.x deletes the Supervisor's refresh token
(upstream home-assistant/core#179219). The Supervisor build these channels
ship can only authenticate to Core with that token, so Supervisor -> Core
breaks. Rolling back to 2026.8 does not repair it: the token removal is a
persistent data migration. A channel file that offers Core >= 2026.9.0 pushes
every device that follows it through a one-way door.

What it checks: every channel file (top-level *.json with a `homeassistant`
or `channel` key) on the tree under test. Every `homeassistant.<machine>`
value, and the top-level `core`, must be < CEILING — compared as a tuple of
integers, never as a string or float.

Exceptions: an entry in the allowlist (scripts/core_ceiling_allowlist.json)
naming file + machine + exact version + a non-empty reason + an expiry date
(YYYY-MM-DD). An expired entry FAILS, used or not. Widening the allowlist is a
deliberate, reviewable act.

Fails CLOSED: no channel files found, an unreadable file, a version that does
not parse, or a malformed allowlist entry is an error, never a skip.

Usage: check_core_ceiling.py [--root DIR] [--allowlist FILE]
"""
import argparse
import datetime
import json
import pathlib
import sys

CEILING = (2026, 9, 0)
MARKER = "CORE-CEILING"  # the self-test asserts THIS guard fired, not just "something failed"
REPO = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_ALLOWLIST = REPO / "scripts" / "core_ceiling_allowlist.json"


def parse_version(v):
    """'2026.8.2.1' -> (2026, 8, 2, 1). Raises ValueError on anything else."""
    if not isinstance(v, str) or not v:
        raise ValueError(f"not a version string: {v!r}")
    parts = v.split(".")
    if not all(p.isdigit() for p in parts):
        raise ValueError(f"not a dotted-integer version: {v!r}")
    return tuple(int(p) for p in parts)


def at_or_above_ceiling(v, ceiling=CEILING):
    """Numeric compare, zero-padded so that 2026.9 == 2026.9.0."""
    a = parse_version(v)
    n = max(len(a), len(ceiling))
    return a + (0,) * (n - len(a)) >= tuple(ceiling) + (0,) * (n - len(ceiling))


def load_allowlist(path, today, errors):
    p = pathlib.Path(path)
    if not p.is_file():
        errors.append(f"allowlist {p} missing (fail closed)")
        return {}
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        errors.append(f"allowlist {p} is not valid JSON ({e})")
        return {}
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        errors.append(f"allowlist {p}: top level must be {{\"entries\": [...]}}")
        return {}
    allowed = {}
    for i, e in enumerate(entries):
        where = f"allowlist entry #{i}"
        if not isinstance(e, dict):
            errors.append(f"{where}: not an object")
            continue
        missing = [k for k in ("file", "machine", "version", "reason", "expires")
                   if not isinstance(e.get(k), str) or not e.get(k).strip()]
        if missing:
            errors.append(f"{where}: missing or empty {missing} — reason and expiry are mandatory")
            continue
        try:
            expires = datetime.date.fromisoformat(e["expires"])
        except ValueError:
            errors.append(f"{where}: expires={e['expires']!r} is not YYYY-MM-DD")
            continue
        if expires < today:
            errors.append(f"{where} ({e['file']} {e['machine']}={e['version']}) EXPIRED on "
                          f"{expires} — remove it or renew it with a fresh reason")
            continue
        allowed[(e["file"], e["machine"], e["version"])] = e
    return allowed


def channel_files(root):
    found = []
    for p in sorted(pathlib.Path(root).glob("*.json")):
        try:
            data = json.loads(p.read_text())
        except json.JSONDecodeError as e:
            sys.exit(f"FAIL {MARKER}: {p.name} is not valid JSON ({e}) — fail closed")
        if isinstance(data, dict) and ("homeassistant" in data or "channel" in data):
            found.append((p.name, data))
    return found


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(REPO), help="directory holding the channel *.json")
    ap.add_argument("--allowlist", default=str(DEFAULT_ALLOWLIST))
    args = ap.parse_args(argv)

    today = datetime.date.today()
    errors = []
    allowed = load_allowlist(args.allowlist, today, errors)

    files = channel_files(args.root)
    if not files:
        errors.append(f"no channel files found under {args.root} — nothing inspected (fail closed)")

    inspected = 0
    used = set()
    ceiling = ".".join(map(str, CEILING))
    for name, ch in files:
        ha = ch.get("homeassistant")
        if not isinstance(ha, dict) or not ha:
            errors.append(f"{name}: no `homeassistant` machine map — cannot inspect (fail closed)")
            continue
        values = list(ha.items())
        if "core" in ch:
            values.append(("core", ch["core"]))
        for machine, ver in values:
            inspected += 1
            label = f"homeassistant.{machine}" if machine != "core" else "core"
            try:
                hit = at_or_above_ceiling(ver)
            except ValueError as e:
                errors.append(f"{name}: {label}: {e} (fail closed)")
                continue
            if not hit:
                continue
            key = (name, machine, ver)
            if key in allowed:
                used.add(key)
                print(f"ALLOWED: {name} {label}={ver} — {allowed[key]['reason']} "
                      f"(expires {allowed[key]['expires']})")
                continue
            errors.append(f"{name}: {label}={ver} is >= {ceiling}")

    for key in sorted(set(allowed) - used):
        print(f"NOTE: allowlist entry {key} matches nothing — consider removing it")

    if errors:
        print(f"FAIL {MARKER}: Core >= {ceiling} deletes the Supervisor's refresh token "
              f"(upstream core#179219); a rollback does not repair it.")
        print("\n".join(f"  {e}" for e in errors))
        return 1
    print(f"OK {MARKER}: {inspected} Core versions in {len(files)} channel files "
          f"({', '.join(n for n, _ in files)}) are all < {ceiling}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
