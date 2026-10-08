#!/usr/bin/env python3
"""Self-test for scripts/check_core_ceiling.py — runs the LIVE script, never a copy.

must-flag/*: every case must exit 1 AND print the CORE-CEILING marker.
must-pass/*: every case must exit 0.
Zero cases in either set is a failure (a self-test over nothing proves nothing).
"""
import pathlib
import subprocess
import sys
import types

REPO = pathlib.Path(__file__).resolve().parent.parent
CHECK = REPO / "scripts" / "check_core_ceiling.py"
FIX = REPO / "tests" / "fixtures" / "core-ceiling"

if not CHECK.is_file():
    sys.exit(f"FAIL: live check {CHECK} not found — cannot self-test (fail closed)")

# Compile the source text directly: an import could serve a stale __pycache__
# entry (same size + same mtime second) instead of the file on disk.
live = types.ModuleType("check_core_ceiling")
live.__file__ = str(CHECK)
exec(compile(CHECK.read_text(), str(CHECK), "exec"), live.__dict__)
MARKER = live.MARKER

failures = []

# Numeric comparison against the live function (tuple of int, not string/float).
for v, want in [("2026.10.0", True), ("2026.9.4", True), ("2026.9.0", True),
                ("2026.9", True), ("2027.1.0", True),
                ("2026.8.2.1", False), ("2026.8.99", False), ("2025.11.3", False)]:
    got = live.at_or_above_ceiling(v)
    if got is not want:
        failures.append(f"at_or_above_ceiling({v!r}) = {got}, want {want}")
for bad in ("2026.9.0b1", "", "dev", None, 2026.9):
    try:
        live.at_or_above_ceiling(bad)
        failures.append(f"at_or_above_ceiling({bad!r}) did not raise")
    except ValueError:
        pass


def run(case):
    return subprocess.run(
        [sys.executable, str(CHECK), "--root", str(case), "--allowlist", str(case / "allowlist.json")],
        capture_output=True, text=True)


counts = {}
for kind, want_rc in (("must-flag", 1), ("must-pass", 0)):
    cases = sorted(p for p in (FIX / kind).glob("*") if p.is_dir())
    counts[kind] = len(cases)
    if not cases:
        failures.append(f"{kind}: zero fixture cases found under {FIX / kind}")
    for case in cases:
        r = run(case)
        out = r.stdout + r.stderr
        if r.returncode != want_rc:
            failures.append(f"{kind}/{case.name}: exit {r.returncode}, want {want_rc}\n{out}")
        elif want_rc == 1 and f"FAIL {MARKER}" not in out:
            failures.append(f"{kind}/{case.name}: failed, but not via {MARKER}\n{out}")
        else:
            print(f"ok  {kind}/{case.name} (exit {r.returncode})")

if failures:
    print("SELF-TEST FAILED:")
    print("\n".join(f"  {f}" for f in failures))
    sys.exit(1)
print(f"OK: self-test — {counts['must-flag']} must-flag, {counts['must-pass']} must-pass cases")
