#!/usr/bin/env python3
"""Self-test for scripts/check_line_channel.py - runs the LIVE script.

must-flag/<case>/line/1.6/*.json: exit 1 (or 2 for no-files) AND the LINE-CHANNEL marker.
must-pass/<case>/line/1.6/*.json: exit 0.
Cases are derived from the REAL line/1.6/stable.json by a single-field mutation
(see MUTATIONS), so a fixture cannot drift away from the real file's shape; the
hand-written fixtures under tests/fixtures/line-channel cover the rest.
Zero cases is a failure.
"""
import copy, json, pathlib, subprocess, sys, tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
CHECK = REPO / "scripts" / "check_line_channel.py"
REAL = REPO / "line" / "1.6" / "stable.json"
FIX = REPO / "tests" / "fixtures" / "line-channel"
if not CHECK.is_file() or not REAL.is_file():
    sys.exit("FAIL: live check or real channel file missing (fail closed)")
real = json.loads(REAL.read_text())

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
    "core-2026.9-old-supervisor": mut(lambda d: d["homeassistant"].update(tinker="2026.9.1")),
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
    "rc-label-with-machine-template-core": mut(lambda d: (d.update(ga_release="BOSv1.6.0-rc1"),
                                                      d["image"].update(core="ghcr.io/home-assistant/{machine}-homeassistant"),
                                                      d["images"].update(core="ghcr.io/home-assistant/{machine}-homeassistant"))),
}

def run(root):
    return subprocess.run([sys.executable, str(CHECK), "--root", str(root)], capture_output=True, text=True)

fails, n = [], {"must-flag": 0, "must-pass": 0}
def case(kind, name, files):
    with tempfile.TemporaryDirectory() as t:
        d = pathlib.Path(t) / "line" / "1.6"; d.mkdir(parents=True)
        for fn, content in files.items():
            (d / fn).write_text(content if isinstance(content, str) else json.dumps(content))
        r = run(t); out = r.stdout + r.stderr; n[kind] += 1
        want = (r.returncode in (1, 2) and "LINE-CHANNEL" in out) if kind == "must-flag" else r.returncode == 0
        if not want:
            fails.append(f"{kind}/{name}: rc={r.returncode}\n{out}")

for name, d in MUST_FLAG.items(): case("must-flag", name, {"stable.json": d})
for name, d in MUST_PASS.items(): case("must-pass", name, {"stable.json": d})
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
