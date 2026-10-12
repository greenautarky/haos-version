#!/usr/bin/env python3
"""Fixtures for check_candidate_channels_agree.py — runs the LIVE script."""
import json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "check_candidate_channels_agree.py")
BASE = {"supervisor": "2025.11.5.9", "homeassistant": {"tinker": "2026.8.2.1"}, "dns": "2026.09.1"}


def run(files):
    with tempfile.TemporaryDirectory() as d:
        for name, content in files.items():
            with open(os.path.join(d, name), "w") as fh:
                fh.write(content if isinstance(content, str) else json.dumps(content))
        return subprocess.run([sys.executable, SCRIPT, d], capture_output=True, text=True).returncode


def ch(c, **over):
    d = json.loads(json.dumps(BASE)); d["channel"] = c
    for k, v in over.items():
        d[k] = v
    return d


CASES = [
    ("must-pass: identical except channel", {"stable.json": ch("stable"), "beta.json": ch("beta"), "dev.json": ch("dev")}, 0),
    ("must-flag: dev supervisor behind", {"stable.json": ch("stable"), "beta.json": ch("beta"), "dev.json": ch("dev", supervisor="2025.11.5.7")}, 1),
    ("must-flag: beta core differs", {"stable.json": ch("stable"), "beta.json": ch("beta", homeassistant={"tinker": "2025.11.3"}), "dev.json": ch("dev")}, 1),
    ("must-flag: key missing in one file", {"stable.json": ch("stable"), "beta.json": {k: v for k, v in ch("beta").items() if k != "dns"}, "dev.json": ch("dev")}, 1),
    ("must-flag: file missing", {"stable.json": ch("stable"), "beta.json": ch("beta")}, 2),
    ("must-flag: unparseable", {"stable.json": ch("stable"), "beta.json": "{", "dev.json": ch("dev")}, 2),
]

fail = 0
for name, files, want in CASES:
    got = run(files)
    ok = got == want
    fail += not ok
    print(("PASS " if ok else "FAIL ") + f"{name}: exit {got} (want {want})")
print(f"{len(CASES) - fail}/{len(CASES)} cases")
sys.exit(1 if fail else 0)
