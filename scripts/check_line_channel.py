#!/usr/bin/env python3
"""Lint the BOSv1.6 line channel files (line/1.6/*.json).

The 1.6 line is a PERMANENT separate path, not a copy of main/ that is promoted
later: main keeps serving every 1.5.x device, and a 1.5.x device must never read
a 2026.10 Supervisor from it (one-way door). Only a 1.6 image is baked with
.../haos-version/main/line/1.6/ as its /etc/ga-version-url, so only devices
running a 1.6 OS poll these files.

What it checks, per channel file (fail closed, every unreadable thing is an error):
  * at least one channel file exists, and stable.json is among them
  * valid JSON; `channel` equals the file name; no unknown channel name
  * `ga_release` is a 1.6 label (BOSv1.6.N, optionally -rcN / -devN)
  * the keys the bake and the device read exist: supervisor, the five plugins,
    homeassistant.{default,tinker,qemuarm-64} (both boards' machines), images
    with {arch} templates, hassos.{ihost,dsgw210} on the 18.x base, ota template
  * a Core >= 2026.9 needs a Supervisor >= 2026.10 in the same file: Core 2026.9
    removes the refresh token the 2025.11 Supervisor logs in with. This is the
    line's replacement for scripts/check_core_ceiling.py, which stays strict on
    the top-level (1.5) files and does not look into line/.

Every finding prints the marker LINE-CHANNEL so the self-test can assert THIS
guard fired, not merely that something failed.

Usage: check_line_channel.py [--root DIR]     (DIR contains line/1.6/)
Exit: 0 clean, 1 findings, 2 cannot check.
"""
import argparse
import json
import pathlib
import re
import sys

MARKER = "LINE-CHANNEL"
LINE_DIR = pathlib.Path("line") / "1.6"
CHANNELS = ("stable", "beta", "dev")
REL_RE = re.compile(r"^BOSv1\.6\.[0-9]+(-(rc|dev)[0-9]+)?$")
PLUGINS = ("cli", "dns", "audio", "multicast", "observer")
MACHINES = ("default", "tinker", "qemuarm-64")  # tinker = iHost, qemuarm-64 = DSGW-210
BOARDS = ("ihost", "dsgw210")


def ver(v):
    if not isinstance(v, str) or not v or not all(p.isdigit() for p in v.split(".")):
        raise ValueError(f"not a dotted-integer version: {v!r}")
    return tuple(int(p) for p in v.split("."))


def ge(a, b):
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) >= b + (0,) * (n - len(b))


def check_file(path, name):
    errs = []
    def e(msg):
        errs.append(f"{MARKER} {name}: {msg}")
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as ex:
        return [f"{MARKER} {name}: unreadable or invalid JSON ({ex})"]
    if not isinstance(d, dict):
        return [f"{MARKER} {name}: top level is not an object"]
    if d.get("channel") != path.stem:
        e(f"channel is {d.get('channel')!r}, the file name says {path.stem!r}")
    if not isinstance(d.get("ga_release"), str) or not REL_RE.match(d["ga_release"]):
        e(f"ga_release {d.get('ga_release')!r} is not a BOSv1.6.x label")
    sup = None
    try:
        sup = ver(d.get("supervisor"))
    except ValueError as ex:
        e(f"supervisor: {ex}")
    for k in PLUGINS:
        try:
            ver(d.get(k).replace("-", ".") if isinstance(d.get(k), str) else d.get(k))
        except (ValueError, AttributeError) as ex:
            e(f"{k}: missing or unparseable ({ex})")
    ha = d.get("homeassistant")
    cores = []
    if not isinstance(ha, dict):
        e("homeassistant map missing")
    else:
        for m in MACHINES:
            if m not in ha:
                e(f"homeassistant.{m} missing (iHost=tinker, DSGW-210=qemuarm-64)")
        for m, v in ha.items():
            try:
                cores.append((m, ver(v)))
            except ValueError as ex:
                e(f"homeassistant.{m}: {ex}")
    if sup is not None:
        for m, c in cores:
            if ge(c, (2026, 9, 0)) and not ge(sup, (2026, 10, 0)):
                e(f"homeassistant.{m} is >= 2026.9 but supervisor is "
                  f"{d['supervisor']} (< 2026.10): Core 2026.9 removes the token that Supervisor logs in with")
    hs = d.get("hassos")
    if not isinstance(hs, dict):
        e("hassos map missing")
    else:
        for b in BOARDS:
            v = hs.get(b)
            if not isinstance(v, str) or not v.startswith("18."):
                e(f"hassos.{b} is {v!r}, want an 18.x version")
    img = d.get("images")
    if not isinstance(img, dict):
        e("images map missing")
    else:
        for k in ("supervisor",) + PLUGINS:
            if "{arch}" not in str(img.get(k, "")):
                e(f"images.{k} has no {{arch}} template")
        if not img.get("core"):
            e("images.core missing")
    ota = d.get("ota")
    if not isinstance(ota, str) or "{version}" not in ota or "{board}" not in ota:
        e("ota template must contain {version} and {board}")
    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(pathlib.Path(__file__).resolve().parent.parent))
    a = ap.parse_args()
    d = pathlib.Path(a.root) / LINE_DIR
    files = sorted(d.glob("*.json")) if d.is_dir() else []
    if not files:
        print(f"{MARKER} no channel files under {d} (fail closed)")
        return 2
    errs = []
    for f in files:
        if f.stem not in CHANNELS:
            errs.append(f"{MARKER} {f.name}: not a channel name {CHANNELS}")
            continue
        errs += check_file(f, f.name)
    if not (d / "stable.json").is_file():
        errs.append(f"{MARKER} stable.json missing")
    for x in errs:
        print(x)
    print(f"line channel: {len(files)} file(s) inspected, {len(errs)} finding(s)")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
