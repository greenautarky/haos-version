#!/usr/bin/env python3
"""Self-test for scripts/check_core_digests.py - runs the LIVE script as a subprocess.

Offline cases are single-field mutations of the REAL line/1.6/rc.json, dev.json and stable.json
(so a fixture cannot drift from the real shape). Online cases run the live script against a
local fake registry (real HTTP, token endpoint, manifests by tag and by digest) that serves a
manifest shaped like the real Core manifest; its digest is computed here, never read from the
script. Every must-flag asserts THAT rule's text fired, not merely that something failed.
Zero cases in a set is a failure (rule 9).
"""
import copy, hashlib, http.server, json, pathlib, subprocess, sys, tempfile, threading

REPO = pathlib.Path(__file__).resolve().parent.parent
CHECK = REPO / "scripts" / "check_core_digests.py"
LINE = REPO / "line" / "1.6"
if not CHECK.is_file() or not (LINE / "rc.json").is_file():
    sys.exit("FAIL: live check or real channel files missing (fail closed)")
real = {n: json.loads((LINE / f"{n}.json").read_text()) for n in ("stable", "rc", "dev")}

A_DG = "sha256:" + "a" * 64
fails, n = [], {"must-flag": 0, "must-pass": 0}


def run(root, *extra):
    return subprocess.run([sys.executable, str(CHECK), "--root", str(root), *extra], capture_output=True, text=True)


def case(kind, name, files, want=None, extra=(), rc_expect=None):
    """files: {channel: dict}; want: text that must be in the output (must-flag)."""
    with tempfile.TemporaryDirectory() as t:
        d = pathlib.Path(t) / "line" / "1.6"
        d.mkdir(parents=True)
        for ch, doc in files.items():
            (d / f"{ch}.json").write_text(doc if isinstance(doc, str) else json.dumps(doc))
        r = run(t, *extra)
    out = r.stdout + r.stderr
    n[kind] += 1
    if kind == "must-flag":
        ok = r.returncode == (rc_expect or 1) and "CORE-DIGEST" in out and (want is None or want in out)
    else:
        ok = r.returncode == 0 and "0 finding(s)" in out
    if not ok:
        fails.append(f"{kind}/{name}: rc={r.returncode} want={want!r}\n{out}")


def mut(ch, f):
    d = copy.deepcopy(real[ch]); f(d); return d


BASE = {"stable": real["stable"], "rc": real["rc"], "dev": real["dev"]}


def with_(**over):
    b = dict(BASE); b.update(over); return b


# ---------------------------------------------------------------- offline
case("must-pass", "real-files", BASE)
case("must-pass", "stable-with-valid-digest", with_(stable=mut("stable", lambda d: d.update(digests={"core": {"armv7": A_DG}}))))
case("must-pass", "stable-without-digest-not-yet-required", BASE)
case("must-pass", "stale-qemuarm64-only-warns", with_(rc=mut("rc", lambda d: d["homeassistant"].update({"qemuarm-64": "2026.1.1"}))))

MF = {  # name: (files, rule text that must fire)
    "rc-no-digests": (with_(rc=mut("rc", lambda d: d.pop("digests"))), "DIGEST-REQUIRED"),
    "dev-no-digests": (with_(dev=mut("dev", lambda d: d.pop("digests"))), "DIGEST-REQUIRED"),
    "rc-digests-no-core": (with_(rc=mut("rc", lambda d: d.update(digests={}))), "DIGEST-REQUIRED"),
    "rc-digests-wrong-arch-only": (with_(rc=mut("rc", lambda d: d.update(digests={"core": {"aarch64": A_DG}}))), "DIGEST-REQUIRED"),
    "rc-aarch64-digest-in-armv7-file": (with_(rc=mut("rc", lambda d: d["digests"]["core"].update(aarch64=A_DG))), "DIGEST-IMAGE"),
    "dev-armv7-digest-in-aarch64-file": (with_(dev=mut("dev", lambda d: d["digests"]["core"].update(armv7=A_DG))), "DIGEST-IMAGE"),
    "rc-digest-uppercase": (with_(rc=mut("rc", lambda d: d["digests"]["core"].update(armv7="sha256:" + "A" * 64))), "DIGEST-FORMAT"),
    "rc-digest-short": (with_(rc=mut("rc", lambda d: d["digests"]["core"].update(armv7="sha256:abc"))), "DIGEST-FORMAT"),
    "rc-digest-no-prefix": (with_(rc=mut("rc", lambda d: d["digests"]["core"].update(armv7="a" * 64))), "DIGEST-FORMAT"),
    "rc-digest-not-string": (with_(rc=mut("rc", lambda d: d["digests"]["core"].update(armv7=None))), "DIGEST-FORMAT"),
    "rc-digests-not-object": (with_(rc=mut("rc", lambda d: d.update(digests="sha256:x"))), "DIGEST-FORMAT"),
    "rc-core-digests-not-object": (with_(rc=mut("rc", lambda d: d["digests"].update(core=[A_DG]))), "DIGEST-FORMAT"),
    "rc-unknown-arch-key": (with_(rc=mut("rc", lambda d: d["digests"]["core"].update(amd64=A_DG))), "unknown arch"),
    "stable-bad-digest-when-present": (with_(stable=mut("stable", lambda d: d.update(digests={"core": {"armv7": "sha256:zz"}}))), "DIGEST-FORMAT"),
    "rc-image-is-aarch64": (with_(rc=mut("rc", lambda d: (d["images"].update(core="ghcr.io/greenautarky/home-assistant-aarch64"),))), "DIGEST-IMAGE"),
    "rc-image-singular-differs": (with_(rc=mut("rc", lambda d: d["image"].update(core="ghcr.io/greenautarky/other"))), "DIGEST-IMAGE"),
    # CORE-AGREE: the version written in two places
    "rc-core-differs-from-default": (with_(rc=mut("rc", lambda d: d.update(core="2026.10.4.5"))), "CORE-AGREE"),
    "rc-tinker-differs": (with_(rc=mut("rc", lambda d: d["homeassistant"].update(tinker="2026.10.4.5"))), "homeassistant.tinker"),
    "rc-default-differs": (with_(rc=mut("rc", lambda d: d["homeassistant"].update(default="2026.10.4.5"))), "homeassistant.default"),
    "rc-no-core-key": (with_(rc=mut("rc", lambda d: d.pop("core"))), "`core` missing"),
    "stable-core-differs": (with_(stable=mut("stable", lambda d: d.update(core="2026.10.4.5"))), "CORE-AGREE"),
    "dev-core-differs": (with_(dev=mut("dev", lambda d: d.update(core="2026.10.4.5"))), "CORE-AGREE"),
    "dev-qemuarm64-differs": (with_(dev=mut("dev", lambda d: d["homeassistant"].update({"qemuarm-64": "2026.10.4.5"}))), "homeassistant.qemuarm-64"),
    "invalid-json": (with_(rc="{nope"), "invalid JSON"),
}
for name, (files, want) in MF.items():
    case("must-flag", name, files, want)
case("must-flag", "no-channel-files", {}, "no channel files", rc_expect=2)

# ---------------------------------------------------------------- online (fake registry)

def manifest(layer_type="application/vnd.docker.image.rootfs.diff.tar.gzip", media="application/vnd.docker.distribution.manifest.v2+json"):
    return json.dumps({"schemaVersion": 2, "mediaType": media,
                       "config": {"mediaType": "application/vnd.docker.container.image.v1+json", "size": 3, "digest": "sha256:" + "c" * 64},
                       "layers": [{"mediaType": layer_type, "size": 5, "digest": "sha256:" + "d" * 64}]}, indent=3).encode()


class Reg:
    """routes: {path: (status, headers, body)}; token_status for /token."""
    def __init__(self, routes, token_status=200):
        reg = self
        reg.routes, reg.token_status = routes, token_status

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self):
                if self.path.startswith("/token"):
                    body = json.dumps({"token": "t"}).encode() if reg.token_status == 200 else b"{}"
                    self.send_response(reg.token_status); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
                st, hdrs, body = reg.routes.get(self.path, (404, {}, b"{}"))
                self.send_response(st)
                for k, v in hdrs.items(): self.send_header(k, v)
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self): self.srv.shutdown()


def sha(b): return "sha256:" + hashlib.sha256(b).hexdigest()


def fake(rc_body, dev_body, tag_rc=None, tag_dev=None, header_rc=True, by_digest_rc=True, **kw):
    """A registry holding the tags the REAL files name, with bodies given here."""
    rt, dt = real["rc"]["core"], real["dev"]["core"]
    rrepo, drepo = "greenautarky/home-assistant-armv7", "greenautarky/home-assistant-aarch64"
    routes = {}
    for repo, tag, body in ((rrepo, rt, rc_body), (drepo, dt, dev_body)):
        h = {"Docker-Content-Digest": sha(body)} if header_rc else {}
        routes[f"/v2/{repo}/manifests/{tag}"] = (200, h, body)
        if by_digest_rc:
            routes[f"/v2/{repo}/manifests/{sha(body)}"] = (200, h, body)
    return Reg(routes, **kw)


def online_files(rc_body, dev_body):
    """Real files, with the declared digests set to the hashes of the bodies the fake serves."""
    return {"stable": real["stable"],
            "rc": mut("rc", lambda d: d["digests"]["core"].update(armv7=sha(rc_body))),
            "dev": mut("dev", lambda d: d["digests"]["core"].update(aarch64=sha(dev_body)))}


GOOD_RC, GOOD_DEV = manifest(), manifest(media="application/vnd.oci.image.manifest.v1+json")
r = fake(GOOD_RC, GOOD_DEV)
case("must-pass", "online-known-good", online_files(GOOD_RC, GOOD_DEV), extra=("--registry", "--registry-url", r.url))
r.close()

# wrong digest: the file pins other bytes than the tag serves
r = fake(GOOD_RC, GOOD_DEV)
case("must-flag", "online-digest-differs-from-registry", dict(online_files(GOOD_RC, GOOD_DEV), rc=mut("rc", lambda d: d["digests"]["core"].update(armv7=A_DG))),
     "DIGEST-REGISTRY digests.core.armv7", ("--registry", "--registry-url", r.url))
r.close()
# the digest header lies (registry/CDN disagreement)
r = fake(GOOD_RC, GOOD_DEV)
r.routes[f"/v2/greenautarky/home-assistant-armv7/manifests/{real['rc']['core']}"] = (200, {"Docker-Content-Digest": A_DG}, GOOD_RC)
case("must-flag", "online-header-disagrees", online_files(GOOD_RC, GOOD_DEV), "registry header says", ("--registry", "--registry-url", r.url))
r.close()
# tag missing in the registry
r = fake(GOOD_RC, GOOD_DEV)
del r.routes[f"/v2/greenautarky/home-assistant-armv7/manifests/{real['rc']['core']}"]
case("must-flag", "online-tag-404", online_files(GOOD_RC, GOOD_DEV), "answers HTTP 404", ("--registry", "--registry-url", r.url))
r.close()
# by-digest fetch not served
r = fake(GOOD_RC, GOOD_DEV, by_digest_rc=False)
case("must-flag", "online-by-digest-404", online_files(GOOD_RC, GOOD_DEV), "answers HTTP 404 (the prefetch fetches by digest)", ("--registry", "--registry-url", r.url))
r.close()
# an index/list instead of one manifest
IDX = json.dumps({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json", "manifests": []}).encode()
r = fake(IDX, GOOD_DEV)
case("must-flag", "online-index-not-stageable", online_files(IDX, GOOD_DEV), "is not a single image manifest", ("--registry", "--registry-url", r.url))
r.close()
# a zstd layer
ZS = manifest(layer_type="application/vnd.oci.image.layer.v1.tar+zstd", media="application/vnd.oci.image.manifest.v1+json")
r = fake(ZS, GOOD_DEV)
case("must-flag", "online-zstd-layer", online_files(ZS, GOOD_DEV), "non-gzip layer types", ("--registry", "--registry-url", r.url))
r.close()
# token refused -> cannot check (exit 2), never a quiet pass
r = fake(GOOD_RC, GOOD_DEV, token_status=403)
case("must-flag", "online-token-403-fails-closed", online_files(GOOD_RC, GOOD_DEV), "cannot reach the registry", ("--registry", "--registry-url", r.url), rc_expect=2)
r.close()
# nothing listening
case("must-flag", "online-unreachable-fails-closed", online_files(GOOD_RC, GOOD_DEV), "cannot reach the registry", ("--registry", "--registry-url", "http://127.0.0.1:9"), rc_expect=2)
# --registry with nothing to compare must not pass (no digest anywhere -> exit 2, not a green zero)
case("must-flag", "online-zero-compared-fails-closed",
     {"stable": real["stable"], "rc": mut("rc", lambda d: d.pop("digests")), "dev": mut("dev", lambda d: d.pop("digests"))},
     "no digest was compared", ("--registry", "--registry-url", "http://127.0.0.1:9"), rc_expect=2)

for f in fails: print("FAIL", f)
if not n["must-flag"] or not n["must-pass"]:
    fails.append(f"zero cases in a set: {n}")
print(f"core-digest self-test: {n['must-flag']} must-flag, {n['must-pass']} must-pass, {len(fails)} failure(s)")
sys.exit(1 if fails else 0)
