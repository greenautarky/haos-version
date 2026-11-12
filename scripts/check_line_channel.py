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
  * rc.json (Odoo #1610): the file an rc BAKE reads. Devices never poll it. It is
    validated like stable.json, and its ga_release must be an -rcN label.
  * PROMOTION (Odoo #1610): stable.json is what every device of the line polls, so
    it may only name a set that an rc proved. line/1.6/promoted.json records which
    rc (ga_release) and the digest of its version-bearing content; stable.json must
    hash to that digest, and the record must carry the gate evidence (or be the one
    pinned pre-gate baseline). A stable.json edit without a matching record fails.
  * `ga_release` is a 1.6 label (BOSv1.6.N, optionally -rcN / -devN)
  * the keys the bake and the device read exist: supervisor, the five plugins,
    homeassistant.{default,tinker,qemuarm-64} (both boards' machines), images
    with {arch} templates, hassos.ihost (and dsgw210 when listed) on the 18.x base, ota template
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
import hashlib
import json
import pathlib
import re
import sys

MARKER = "LINE-CHANNEL"
LINE_DIR = pathlib.Path("line") / "1.6"
CHANNELS = ("stable", "rc", "beta", "dev")
RC_REL_RE = re.compile(r"^BOSv1\.6\.[0-9]+-rc[0-9]+$")
PROMOTED = "promoted.json"
# The one stable.json that was never gated: the set rc1 and rc2 devices already run
# (before #1610 existed). PINNED here, never read from the record: a record may not
# declare itself a baseline for any other release.
BASELINE_RELEASE = "BOSv1.6.0-rc1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REL_RE = re.compile(r"^BOSv1\.6\.[0-9]+(-(rc|dev)[0-9]+)?$")
PLUGINS = ("cli", "dns", "audio", "multicast", "observer")
MACHINES = ("default", "tinker", "qemuarm-64")  # tinker = iHost, qemuarm-64 = DSGW-210
BOARDS = ("ihost",)            # required on the line
OPTIONAL_BOARDS = ("dsgw210",)  # may be absent: rc1 shipped iHost-only (Thomas 2026-11-05) until the
                                # aarch64 GA image chain (WP9); if present, every rule below applies
# CPU arch per board, PINNED here (never derived from the file under audit):
# buildroot-ihost tinker_defconfig = armv7, buildroot-dsgw210 dsgw210_defconfig =
# BR2_aarch64 / HASSIO_MACHINE qemuarm-64. The GA Core fork exists for armv7 only;
# an aarch64 board runs the upstream Core (plan 2026-10-17, WP8 channel content),
# and the Supervisor refuses an armv7 Core on aarch64 (DUT2, 2026-10-12).
BOARD_ARCH = {"ihost": "armv7", "dsgw210": "aarch64"}
# dev.json (DEV-SCOPE): the DSGW-210 dev channel. Pinned here, never derived from the file.
DEV_FILE_STEM = "dev"
DEV_CORE_IMAGE = "ghcr.io/greenautarky/home-assistant-aarch64"
# A plain CalVer tag, NOT a `-dev` one: the Supervisor parses Core tags with AwesomeVersion, which
# rates "2026.10.4.3-dev" UNKNOWN. get_latest_version() then skips the baked image (-> landing
# page + runtime pull) and `ha core start` raises AwesomeVersionCompareException (DUT2,
# BOSv1.6.0-dev9, 2026-11-12, Odoo #1985).
DEV_CORE_RE = re.compile(r"^[0-9]{4}\.[0-9]{1,2}\.[0-9]+(\.[0-9]+)?$")
DEV_REL_RE = re.compile(r"^BOSv1\.6\.[0-9]+-dev[0-9]+$")
UPSTREAM_CORE_RE = re.compile(r"^[0-9]{4}\.[0-9]{1,2}\.[0-9]+$")  # upstream calver, no GA .N suffix


def component_digest(d):
    """sha256 over the version-bearing content of a channel file: everything except the
    `channel` label (a promoted stable.json is its rc.json with channel renamed)."""
    body = {k: v for k, v in d.items() if k != "channel"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


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
    dev_scope = path.stem == DEV_FILE_STEM
    if not isinstance(d.get("ga_release"), str) or not REL_RE.match(d["ga_release"]):
        e(f"ga_release {d.get('ga_release')!r} is not a BOSv1.6.x label")
    if path.stem == "rc" and isinstance(d.get("ga_release"), str) and not RC_REL_RE.match(d["ga_release"]):
        e(f"ga_release {d.get('ga_release')!r} is not an -rcN label (rc.json is what an rc bake reads)")
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
        hs0 = d.get("hassos") if isinstance(d.get("hassos"), dict) else {}
        for m in MACHINES:
            if m == "qemuarm-64" and "dsgw210" not in hs0:
                continue  # the DSGW machine is only required while the DSGW board is on the line
            if dev_scope and m == "tinker":
                continue  # DEV-SCOPE: no iHost Core in dev.json (checked below)
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
        for b in (("dsgw210",) if dev_scope else BOARDS + tuple(x for x in OPTIONAL_BOARDS if x in hs)):
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
    # ARCH-CORE: an aarch64 board must not be handed the armv7-only GA Core.
    # A 2025.11 Supervisor takes ONE core image for every machine, so until the
    # line carries the 2026.10 Supervisor with per-arch images (WP9) the file
    # cannot be right for both boards. Allowed on a -devN label (bench only,
    # printed as a WARN), a hard finding for any rc or final.
    is_dev = isinstance(d.get("ga_release"), str) and re.search(r"-dev[0-9]+$", d["ga_release"])
    arch_findings = []
    if dev_scope:
        # DEV-SCOPE: DSGW-210 only. iHosts read stable.json and must not be reachable from here.
        def f(msg):
            errs.append(f"{MARKER} {name}: DEV-SCOPE {msg}")
        if isinstance(hs, dict) and "ihost" in hs:
            f("hassos lists ihost (dev.json is DSGW-210 only; iHosts stay on stable.json)")
        if isinstance(ha, dict):
            if "tinker" in ha:
                f("homeassistant lists tinker (iHost Core) in the DSGW-only dev.json")
            if "qemuarm-64" in ha and not DEV_CORE_RE.match(str(ha["qemuarm-64"])):
                f(f"homeassistant.qemuarm-64 is {ha['qemuarm-64']!r}, want a plain CalVer YYYY.M.P[.N] (the GA aarch64 Core); a -dev tag is UNKNOWN to the Supervisor's AwesomeVersion (Odoo #1985)")
            if ha.get("default") != ha.get("qemuarm-64"):
                f(f"homeassistant.default {ha.get('default')!r} differs from qemuarm-64 {ha.get('qemuarm-64')!r}")
        if not (isinstance(d.get("ga_release"), str) and DEV_REL_RE.match(d["ga_release"])):
            f(f"ga_release {d.get('ga_release')!r} is not a BOSv1.6.N-devN label")
        if d.get("core") != (ha or {}).get("qemuarm-64"):
            f(f"core {d.get('core')!r} differs from homeassistant.qemuarm-64")
        for sect in ("image", "images"):
            ci = (d.get(sect) or {}).get("core")
            if ci != DEV_CORE_IMAGE:
                f(f"{sect}.core is {ci!r}, want {DEV_CORE_IMAGE}")
        if d.get("image") != d.get("images"):
            f("image and images differ (the device would pull other plugin/Core images than the bake carries)")
    elif isinstance(hs, dict) and any(BOARD_ARCH.get(b) == "aarch64" for b in hs):
        for sect in ("image", "images"):
            core_img = str((d.get(sect) or {}).get("core", ""))
            if "armv7" in core_img and "{arch}" not in core_img and "{machine}" not in core_img:
                arch_findings.append(f"{sect}.core is {core_img!r} (armv7-only) but board dsgw210 is aarch64")
    if not dev_scope and isinstance(ha, dict) and "qemuarm-64" in ha and not UPSTREAM_CORE_RE.match(str(ha["qemuarm-64"])):
        arch_findings.append(f"homeassistant.qemuarm-64 is {ha['qemuarm-64']!r}, want an upstream Core version (YYYY.M.P, no GA suffix)")
    for f in arch_findings:
        if is_dev:
            print(f"WARN {MARKER} {name}: ARCH-CORE {f} (allowed on a -devN label; refused for rc/final until WP9)")
        else:
            e(f"ARCH-CORE {f}")
    ota = d.get("ota")
    if not isinstance(ota, str) or "{version}" not in ota or "{board}" not in ota:
        e("ota template must contain {version} and {board}")
    return errs


def check_promotion(d):
    """stable.json must be a set an rc proved (Odoo #1610). Returns findings."""
    M = f"{MARKER} PROMOTION"
    sp, rp = d / "stable.json", d / PROMOTED
    if not rp.is_file():
        return [f"{M}: {PROMOTED} missing - stable.json is device-facing and may only name a promoted set"]
    try:
        rec = json.loads(rp.read_text(encoding="utf-8"))
        stable = json.loads(sp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as ex:
        return [f"{M}: cannot read {PROMOTED} / stable.json ({ex})"]
    if not isinstance(rec, dict) or not isinstance(stable, dict):
        return [f"{M}: {PROMOTED} or stable.json is not an object"]
    errs = []
    rel = rec.get("ga_release")
    if not isinstance(rel, str) or not RC_REL_RE.match(rel):
        errs.append(f"{M}: {PROMOTED} ga_release {rel!r} is not an -rcN label")
    elif stable.get("ga_release") != rel:
        errs.append(f"{M}: stable.json ga_release {stable.get('ga_release')!r} is not the promoted rc {rel!r}")
    want = rec.get("component_digest")
    if not isinstance(want, str) or not SHA256_RE.match(want):
        errs.append(f"{M}: {PROMOTED} component_digest is not a sha256")
    elif component_digest(stable) != want:
        errs.append(f"{M}: stable.json content digest {component_digest(stable)[:12]}... differs from the promoted "
                    f"record {want[:12]}... - stable.json names a set no rc gate has proven (or the record was not updated)")
    gate = rec.get("gate")
    if not isinstance(gate, dict):
        errs.append(f"{M}: {PROMOTED} has no gate object")
    elif gate.get("name") == "baseline":
        if rel != BASELINE_RELEASE:
            errs.append(f"{M}: gate 'baseline' is only allowed for {BASELINE_RELEASE} (pre-gate set), not {rel!r}")
    elif gate.get("name") == "k31-fresh-flash":
        if gate.get("result") != "pass":
            errs.append(f"{M}: gate result is {gate.get('result')!r}, want 'pass'")
        if not (isinstance(gate.get("bundle_sha256"), str) and SHA256_RE.match(gate["bundle_sha256"])):
            errs.append(f"{M}: gate.bundle_sha256 is not a sha256")
        if not (isinstance(gate.get("record"), str) and gate["record"]):
            errs.append(f"{M}: gate.record (pointer to the private gate record) missing")
    else:
        errs.append(f"{M}: gate.name {gate.get('name')!r} is not 'k31-fresh-flash' (or the pinned baseline)")
    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--print-digest", metavar="FILE", help="print the component digest of a channel file and exit")
    ap.add_argument("--root", default=str(pathlib.Path(__file__).resolve().parent.parent))
    a = ap.parse_args()
    if a.print_digest:
        try:
            print(component_digest(json.loads(pathlib.Path(a.print_digest).read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError, AttributeError) as ex:
            print(f"{MARKER} cannot digest {a.print_digest}: {ex}")
            return 2
        return 0
    d = pathlib.Path(a.root) / LINE_DIR
    files = sorted(d.glob("*.json")) if d.is_dir() else []
    if not files:
        print(f"{MARKER} no channel files under {d} (fail closed)")
        return 2
    errs = []
    for f in files:
        if f.name == PROMOTED:
            continue
        if f.stem not in CHANNELS:
            errs.append(f"{MARKER} {f.name}: not a channel name {CHANNELS}")
            continue
        errs += check_file(f, f.name)
    if not (d / "stable.json").is_file():
        errs.append(f"{MARKER} stable.json missing")
    else:
        errs += check_promotion(d)
    for x in errs:
        print(x)
    print(f"line channel: {len([f for f in files if f.name != PROMOTED])} file(s) inspected, {len(errs)} finding(s)")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
