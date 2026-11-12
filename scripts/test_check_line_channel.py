#!/usr/bin/env python3
"""Self-test for scripts/check_line_channel.py - runs the LIVE script.

must-flag/<case>/line/1.6/*.json: exit 1 (or 2 for no-files) AND the LINE-CHANNEL marker.
must-pass/<case>/line/1.6/*.json: exit 0.
Cases are derived from the REAL line/1.6/stable.json by a single-field mutation
(see MUTATIONS), so a fixture cannot drift away from the real file's shape; the
hand-written fixtures under tests/fixtures/line-channel cover the rest.
Zero cases is a failure.
"""
import copy, json, pathlib, re, subprocess, sys, tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
CHECK = REPO / "scripts" / "check_line_channel.py"
REAL = REPO / "line" / "1.6" / "stable.json"
FIX = REPO / "tests" / "fixtures" / "line-channel"
if not CHECK.is_file() or not REAL.is_file():
    sys.exit("FAIL: live check or real channel file missing (fail closed)")
real = json.loads(REAL.read_text())
REAL_DEV = REPO / "line" / "1.6" / "dev.json"
if not REAL_DEV.is_file():
    sys.exit("FAIL: real dev.json missing (fail closed)")
real_dev = json.loads(REAL_DEV.read_text())

REAL_RC = REPO / "line" / "1.6" / "rc.json"
REAL_PROMOTED = REPO / "line" / "1.6" / "promoted.json"
if not REAL_RC.is_file() or not REAL_PROMOTED.is_file():
    sys.exit("FAIL: real rc.json / promoted.json missing (fail closed)")
real_rc = json.loads(REAL_RC.read_text())
real_promoted = json.loads(REAL_PROMOTED.read_text())

def mut(f):
    d = copy.deepcopy(real); f(d); return d

MUST_FLAG = {
    "ga-release-is-1.5": mut(lambda d: d.update(ga_release="BOSv1.5.3")),
    "channel-label-mismatch": mut(lambda d: d.update(channel="beta")),
    "no-tinker-core": mut(lambda d: d["homeassistant"].pop("tinker")),
    # dev label: ARCH-CORE only WARNs there, so ONLY the rule under test can flag these two
    "no-dsgw-machine": mut(lambda d: (d.update(ga_release="BOSv1.6.0-dev2"), d["hassos"].update(dsgw210="18.3.1.6"), d["homeassistant"].pop("qemuarm-64"))),
    "no-ihost-board": mut(lambda d: d["hassos"].pop("ihost")),
    "dsgw-listed-on-16": mut(lambda d: (d.update(ga_release="BOSv1.6.0-dev2"), d["hassos"].update(dsgw210="16.3.1.10"))),
    "hassos-16": mut(lambda d: d["hassos"].update(ihost="16.3.1.10")),
    # state-independent: sets EVERY field the rule reads (old Supervisor + Core >= 2026.9), not the live file's
    "core-2026.9-old-supervisor": mut(lambda d: (d.update(supervisor="2025.11.5.10"), d["homeassistant"].update(default="2026.9.1", tinker="2026.9.1"))),
    "no-arch-template": mut(lambda d: d["images"].update(dns="ghcr.io/x/dns")),
    "ota-no-board": mut(lambda d: d.update(ota="https://x/{version}.raucb")),
    "supervisor-unparseable": mut(lambda d: d.update(supervisor="latest")),
    # ARCH-CORE: rc/final must not hand the aarch64 DSGW the armv7-only GA Core
    "rc-armv7-core-on-aarch64": mut(lambda d: (d.update(ga_release="BOSv1.6.0-rc1"), d["hassos"].update(dsgw210="18.3.1.6"))),
    "final-armv7-core-on-aarch64": mut(lambda d: (d.update(ga_release="BOSv1.6.0"), d["hassos"].update(dsgw210="18.3.1.6"))),
    "rc-qemuarm64-ga-suffix": mut(lambda d: (d.update(ga_release="BOSv1.6.0-rc1"),
                                             d["image"].update(core="ghcr.io/home-assistant/{machine}-homeassistant"), d["images"].update(core="ghcr.io/home-assistant/{machine}-homeassistant"),
                                             d["homeassistant"].update({"qemuarm-64": "2026.8.2.1"}))),
}
MUST_PASS = {
    "real-file": real,
    "dev-label-dsgw-listed": mut(lambda d: (d.update(ga_release="BOSv1.6.0-dev2"), d["hassos"].update(dsgw210="18.3.1.6"))),
    "core-2026.9-with-supervisor-2026.10": mut(lambda d: (d.update(supervisor="2026.10.1.2"),
                                                       d["homeassistant"].update(tinker="2026.9.1"))),
    "rc-label-with-machine-template-core": mut(lambda d: (d.update(ga_release="BOSv1.6.0-rc1", supervisor="2026.10.1.2"),
                                                      d["homeassistant"].update(default="2026.10.4.4", tinker="2026.10.4.4"),
                                                      d["image"].update(core="ghcr.io/home-assistant/{machine}-homeassistant"),
                                                      d["images"].update(core="ghcr.io/home-assistant/{machine}-homeassistant"))),
}

def dmut_rc(f):
    d = copy.deepcopy(real_rc); f(d); return d

def dmut(f):
    d = copy.deepcopy(real_dev); f(d); return d

# dev.json (DEV-SCOPE): each must-flag is ONE mutation of the real dev.json, so only
# the rule under test can flag it.
DEV_MUST_FLAG = {
    "dev-lists-ihost": dmut(lambda d: d["hassos"].update(ihost="18.3.1.6")),
    "dev-lists-tinker-core": dmut(lambda d: d["homeassistant"].update(tinker="2026.10.4.3-dev")),
    "dev-no-dsgw-board": dmut(lambda d: d["hassos"].pop("dsgw210")),
    # Odoo #1985: a -dev tag is not CalVer for the Supervisor (AwesomeVersion UNKNOWN) -> the baked Core is never started
    "dev-core-dev-suffix": dmut(lambda d: (d["homeassistant"].update({"qemuarm-64": "2026.10.4.3-dev", "default": "2026.10.4.3-dev"}), d.update(core="2026.10.4.3-dev"))),
    "dev-core-armv7-image": dmut(lambda d: (d["images"].update(core="ghcr.io/greenautarky/home-assistant-armv7"), d["image"].update(core="ghcr.io/greenautarky/home-assistant-armv7"))),
    "dev-image-images-differ": dmut(lambda d: d["image"].update(dns="homeassistant/{arch}-hassio-dns")),
    "dev-rc-label": dmut(lambda d: d.update(ga_release="BOSv1.6.0-rc1")),
    "dev-core-2026.10-old-supervisor": dmut(lambda d: d.update(supervisor="2025.11.5.10")),
    "dev-default-differs": dmut(lambda d: d["homeassistant"].update(default="2026.8.2.2")),
    "dev-core-key-differs": dmut(lambda d: d.update(core="2026.10.4.2")),
}
DEV_MUST_PASS = {
    "real-dev-file": real_dev,
    "dev-core-3-part-version": dmut(lambda d: (d["homeassistant"].update({"qemuarm-64": "2026.10.5", "default": "2026.10.5"}), d.update(core="2026.10.5"))),
}
# a -dev Core suffix is refused everywhere (dev.json included, see dev-core-dev-suffix)
SUFFIX_ELSEWHERE_MUST_FLAG = {
    "dev-suffix-core-in-stable": mut(lambda d: (d.update(supervisor="2026.10.5.1"), d["homeassistant"].update(tinker="2026.10.4.3-dev"))),
    "dev-suffix-core-in-beta": ("beta.json", mut(lambda d: (d.update(channel="beta", supervisor="2026.10.5.1"), d["homeassistant"].update(tinker="2026.10.4.3-dev")))),
}

def run(root):
    return subprocess.run([sys.executable, str(CHECK), "--root", str(root)], capture_output=True, text=True)

fails, n = [], {"must-flag": 0, "must-pass": 0}
WANT = {  # the specific rule that must fire (not merely "something failed")
    "dev-lists-ihost": "DEV-SCOPE hassos lists ihost",
    "dev-lists-tinker-core": "DEV-SCOPE homeassistant lists tinker",
    "dev-no-dsgw-board": "hassos.dsgw210 is None",
    "dev-core-dev-suffix": "not a dotted-integer version",
    "dev-core-armv7-image": "images.core is 'ghcr.io/greenautarky/home-assistant-armv7'",
    "dev-image-images-differ": "image and images differ",
    "dev-rc-label": "is not a BOSv1.6.N-devN label",
    "dev-core-2026.10-old-supervisor": "supervisor is 2025.11.5.10",
    "dev-default-differs": "homeassistant.default",
    "dev-core-key-differs": "core '2026.10.4.2' differs",
    "dev-suffix-core-in-stable": "homeassistant.tinker: not a dotted-integer version",
    "dev-suffix-core-in-beta": "homeassistant.tinker: not a dotted-integer version",
}
def digest(path):
    r = subprocess.run([sys.executable, str(CHECK), "--print-digest", str(path)], capture_output=True, text=True)
    if r.returncode != 0: sys.exit(f"FAIL: live --print-digest failed: {r.stdout}{r.stderr}")
    return r.stdout.strip()

# must-pass cases about something OTHER than the promotion, whose stable.json carries a -dev label
# (bench-only ARCH-CORE allowance): their only legitimate finding is the PROMOTION rule.
PROMO_EXEMPT = {"dev-label-dsgw-listed"}

def case(kind, name, files):
    with tempfile.TemporaryDirectory() as t:
        d = pathlib.Path(t) / "line" / "1.6"; d.mkdir(parents=True)
        for fn, content in files.items():
            if content is not None:
                (d / fn).write_text(content if isinstance(content, str) else json.dumps(content))
        if "promoted.json" not in files and (d / "stable.json").is_file():
            # every case that is not ABOUT the promotion record gets a matching one, built
            # with the LIVE digest function, so only the rule under test can flag it
            try:
                st = json.loads((d / "stable.json").read_text())
                # release label follows the stable.json under test (when it is an rc label) and the gate is a fixed
                # constant, so a fixture never depends on which rc the live record currently names
                rl = st.get("ga_release") if isinstance(st, dict) and re.match(r"^BOSv1\.6\.[0-9]+-rc[0-9]+$", str(st.get("ga_release"))) else real_promoted["ga_release"]
                (d / "promoted.json").write_text(json.dumps({"ga_release": rl, "component_digest": digest(d / "stable.json"), "gate": {"name": "k31-fresh-flash", "result": "pass", "bundle_sha256": "a" * 64, "record": "ga-ops:gates/line-1.6/fixture.json"}}))
            except (json.JSONDecodeError, SystemExit):
                pass
        r = run(t); out = r.stdout + r.stderr; n[kind] += 1
        want = (r.returncode in (1, 2) and "LINE-CHANNEL" in out) if kind == "must-flag" else r.returncode == 0
        if kind == "must-pass" and name in PROMO_EXEMPT:
            errs = [l for l in out.splitlines() if l.startswith("LINE-CHANNEL")]
            want = r.returncode == 1 and bool(errs) and all("PROMOTION" in l for l in errs)
        if want and name in WANT and WANT[name] not in out:
            want = False; out += f"\n(wanted rule text not found: {WANT[name]!r})"
        if not want:
            fails.append(f"{kind}/{name}: rc={r.returncode}\n{out}")

for name, d in MUST_FLAG.items(): case("must-flag", name, {"stable.json": d})
for name, d in MUST_PASS.items(): case("must-pass", name, {"stable.json": d})
for name, d in DEV_MUST_FLAG.items(): case("must-flag", name, {"stable.json": real, "dev.json": d})
for name, d in DEV_MUST_PASS.items(): case("must-pass", name, {"stable.json": real, "dev.json": d})
for name, d in SUFFIX_ELSEWHERE_MUST_FLAG.items():
    fn, body = d if isinstance(d, tuple) else ("stable.json", d)
    files = {"stable.json": real}; files[fn] = body
    case("must-flag", name, files)
# rc.json (Odoo #1610): validated like stable.json, -rcN label only
RC_MUST_FLAG = {
    "rc-channel-label-mismatch": mut(lambda d: d.update(channel="stable", ga_release="BOSv1.6.0-rc3")),
    "rc-final-label": dmut_rc(lambda d: d.update(ga_release="BOSv1.6.0")),
    "rc-dev-label": dmut_rc(lambda d: d.update(ga_release="BOSv1.6.0-dev2")),
    "rc-no-supervisor-key": dmut_rc(lambda d: d.pop("supervisor")),
    "rc-core-2026.10-old-supervisor": dmut_rc(lambda d: d.update(supervisor="2025.11.5.10")),
    "rc-no-arch-template": dmut_rc(lambda d: d["images"].update(dns="ghcr.io/x/dns")),
}
RC_WANT = {
    "rc-final-label": "is not an -rcN label",
    "rc-dev-label": "is not an -rcN label",
    "rc-core-2026.10-old-supervisor": "supervisor is 2025.11.5.10",
    "rc-channel-label-mismatch": "the file name says 'rc'",
}
for name, d in RC_MUST_FLAG.items():
    WANT[name] = RC_WANT.get(name, WANT.get(name, "LINE-CHANNEL"))
    case("must-flag", name, {"stable.json": real, "rc.json": d})
case("must-pass", "rc-ahead-of-stable", {"stable.json": real, "rc.json": real_rc})
case("must-pass", "rc-equals-stable-set-other-channel-label", {"stable.json": real, "rc.json": dict(real, channel="rc")})
# PROMOTION: stable.json may only name a set an rc proved
GATE = {"name": "k31-fresh-flash", "result": "pass", "bundle_sha256": "a" * 64, "record": "ga-ops:gates/line-1.6/BOSv1.6.0-rc3.json"}
def rec(**kw):
    r = {"ga_release": real["ga_release"], "component_digest": digest(REAL), "gate": real_promoted["gate"]}; r.update(kw); return r
PROMO_MUST_FLAG = {
    "promo-record-missing": ({"stable.json": real, "promoted.json": None}, "promoted.json missing"),
    "promo-stable-edited-without-record": ({"stable.json": mut(lambda d: d.update(supervisor="2026.10.5.1", homeassistant=dict(d["homeassistant"], tinker="2026.10.4.4", default="2026.10.4.4"))), "promoted.json": rec()}, "differs from the promoted"),
    # an rc set that differs from the promoted one (explicitly: a Supervisor no record names), independent of whether live rc == stable
    "promo-stable-is-unproven-rc-set": ({"stable.json": dict(real_rc, channel="stable", supervisor="2026.10.99.99"), "promoted.json": rec()}, "differs from the promoted"),
    "promo-release-mismatch": ({"stable.json": real, "promoted.json": rec(ga_release="BOSv1.6.0-rc2")}, "is not the promoted rc"),
    "promo-digest-not-a-sha256": ({"stable.json": real, "promoted.json": rec(component_digest="xyz")}, "component_digest is not a sha256"),
    "promo-gate-failed": ({"stable.json": real, "promoted.json": rec(gate=dict(GATE, result="fail"))}, "want 'pass'"),
    "promo-gate-no-bundle-digest": ({"stable.json": real, "promoted.json": rec(gate={k: v for k, v in GATE.items() if k != "bundle_sha256"})}, "bundle_sha256"),
    "promo-gate-unknown-name": ({"stable.json": real, "promoted.json": rec(gate={"name": "trust-me"})}, "is not 'k31-fresh-flash'"),
    "promo-record-not-json": ({"stable.json": real, "promoted.json": "{nope"}, "cannot read"),
}
for name, (files, want) in PROMO_MUST_FLAG.items():
    WANT[name] = want
    case("must-flag", name, files)
# the baseline gate is pinned to rc1: rc3 declaring itself a baseline is refused with the baseline rule
s3 = dict(real, ga_release="BOSv1.6.0-rc3")
WANT["promo-baseline-claimed-by-rc3"] = "only allowed for"
case("must-flag", "promo-baseline-claimed-by-rc3", {"stable.json": s3, "promoted.json": {"ga_release": "BOSv1.6.0-rc3", "component_digest": "0" * 64, "gate": {"name": "baseline"}}})
with tempfile.TemporaryDirectory() as t:       # same, but with the digest right: ONLY the baseline rule may fire
    q = pathlib.Path(t) / "s.json"; q.write_text(json.dumps(s3))
    WANT["promo-baseline-claimed-by-rc3-digest-ok"] = "only allowed for"
    case("must-flag", "promo-baseline-claimed-by-rc3-digest-ok", {"stable.json": s3, "promoted.json": {"ga_release": "BOSv1.6.0-rc3", "component_digest": digest(q), "gate": {"name": "baseline"}}})
    # KNOWN-GOOD: a real promotion of the live rc set (gated) passes; the rc label comes from rc.json
    s_promoted = dict(real_rc, channel="stable"); q.write_text(json.dumps(s_promoted))
    case("must-pass", "promo-gated-rc-promoted", {"stable.json": s_promoted, "rc.json": real_rc, "promoted.json": {"ga_release": real_rc["ga_release"], "component_digest": digest(q), "gate": GATE}})
case("must-pass", "promo-real-record", {"stable.json": real, "promoted.json": real_promoted})
case("must-pass", "stable-and-dev-together", {"stable.json": real, "dev.json": real_dev})
case("must-flag", "invalid-json", {"stable.json": "{nope"})
case("must-flag", "only-beta-no-stable", {"beta.json": mut(lambda d: d.update(channel="beta"))})
case("must-flag", "unknown-channel-name", {"stable.json": real, "weird.json": real})
with tempfile.TemporaryDirectory() as t:       # no line dir at all
    r = run(t); n["must-flag"] += 1
    if not (r.returncode == 2 and "LINE-CHANNEL" in r.stdout): fails.append(f"must-flag/no-files: rc={r.returncode}")
for kind in ("must-flag", "must-pass"):         # hand-written extras
    for p in sorted((FIX / kind).glob("*/")) if (FIX / kind).is_dir() else []:
        case(kind, p.name, {f.name: f.read_text() for f in (p / "line" / "1.6").glob("*.json")})
if not n["must-flag"] or not n["must-pass"]:
    fails.append(f"zero cases in a set: {n}")
for f in fails: print("FAIL", f)
print(f"line-channel self-test: {n['must-flag']} must-flag, {n['must-pass']} must-pass, {len(fails)} failure(s)")
sys.exit(1 if fails else 0)
