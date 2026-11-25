#!/usr/bin/env python3
"""Gate for the pinned Core image digest in the line channel files (line/1.6/*.json).

Why (ga-ihost-docs #336 PREFETCH-MVP-DESIGN section 3.1, Odoo #2136): the device-side
Core image prefetch (ga_manager `image-prefetch`, fed by the fleet-manager) verifies every
layer against the digest of the PINNED image, never against what the registry says now.
That needs the manifest digest in the same channel file that names the Core version:

    "digests": {"core": {"armv7": "sha256:<64 hex>"}}        (rc.json, stable.json)
    "digests": {"core": {"aarch64": "sha256:<64 hex>"}}      (dev.json, DSGW-210)

A digest next to a version is a second source for one truth, so this gate COMPARES them
(rule 61): offline it checks the file against itself, online (--registry) against the
registry.

OFFLINE rules (always)
  * DIGEST-FORMAT   `digests` / `digests.core` are objects, keys are known arches,
                    values are `sha256:` + 64 lowercase hex.
  * DIGEST-REQUIRED rc.json needs digests.core.armv7, dev.json needs digests.core.aarch64.
                    stable.json / beta.json: validated when present, not yet required (the
                    PROMOTE workflow copies rc.json, so stable gets the digest by promotion;
                    editing stable.json by hand is refused by check_line_channel.py).
  * DIGEST-IMAGE    the digest's arch matches images.core and image.core of the file
                    (the pinned repo of that arch), so an aarch64 digest cannot sit next
                    to the armv7 image.
  * CORE-AGREE      the Core version is written in two places: top-level `core` and
                    homeassistant.* . They must be equal (armv7 files: default == tinker
                    == core; dev: default == qemuarm-64 == core). The fleet-manager refuses
                    a file where they differ (ambiguous_core_version); this catches it at
                    the producer.
  * STALE-ARCH      (WARN only, never a finding) homeassistant.qemuarm-64 in a non-dev
                    file differs from the Core the file offers. Changing it is a channel
                    VALUE decision, not made by this gate.

ONLINE rules (--registry [--registry-url URL]); unreachable registry is exit 2, never a skip
  * DIGEST-REGISTRY  GET <image>/manifests/<tag> hashes (sha256 of the body bytes) to the
                     declared digest, and the Docker-Content-Digest header agrees.
  * DIGEST-BY-DIGEST GET <image>/manifests/<digest> answers 200 with the same bytes (the
                     prefetch fetches by digest; a tag-only manifest is useless to it).
  * DIGEST-PREFETCHABLE the manifest is ONE docker-v2/OCI image manifest (no index/list), its
                     config and every layer are gzip types: what ga_manager can stage today.

Every finding prints the marker CORE-DIGEST so the self-test can assert THIS guard fired.

Usage: check_core_digests.py [--root DIR] [--registry] [--registry-url URL]
Exit: 0 clean, 1 findings, 2 cannot check.
"""
import argparse
import hashlib
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

MARKER = "CORE-DIGEST"
LINE_DIR = pathlib.Path("line") / "1.6"
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
# PINNED here, never derived from the file under audit (rule 7).
CORE_IMAGE = {
    "armv7": "ghcr.io/greenautarky/home-assistant-armv7",
    "aarch64": "ghcr.io/greenautarky/home-assistant-aarch64",
}
FILE_ARCH = {"rc": "armv7", "stable": "armv7", "beta": "armv7", "dev": "aarch64"}
REQUIRED_IN = ("rc", "dev")
# homeassistant.* keys that name the Core of the file's arch
MACHINE_KEYS = {"armv7": ("default", "tinker"), "aarch64": ("default", "qemuarm-64")}
MANIFEST_TYPES = ("application/vnd.docker.distribution.manifest.v2+json",
                  "application/vnd.oci.image.manifest.v1+json")
CONFIG_TYPES = ("application/vnd.docker.container.image.v1+json", "application/vnd.oci.image.config.v1+json")
GZIP_LAYERS = ("application/vnd.docker.image.rootfs.diff.tar.gzip", "application/vnd.oci.image.layer.v1.tar+gzip")
ACCEPT = ", ".join(MANIFEST_TYPES + (
    "application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json"))
DEFAULT_REGISTRY = "https://ghcr.io"


def check_file(path, name):
    """Offline rules for one channel file. Returns (findings, warnings)."""
    errs, warns = [], []

    def e(rule, msg):
        errs.append(f"{MARKER} {name}: {rule} {msg}")
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as ex:
        return [f"{MARKER} {name}: unreadable or invalid JSON ({ex})"], warns
    if not isinstance(d, dict):
        return [f"{MARKER} {name}: top level is not an object"], warns
    stem = path.stem
    arch = FILE_ARCH.get(stem)
    if arch is None:
        return [f"{MARKER} {name}: not a channel name {tuple(FILE_ARCH)}"], warns

    # ---- CORE-AGREE: the Core version is written twice
    core = d.get("core")
    ha = d.get("homeassistant")
    if not isinstance(core, str) or not core:
        e("CORE-AGREE", "top-level `core` missing")
    if not isinstance(ha, dict):
        e("CORE-AGREE", "homeassistant map missing")
    elif isinstance(core, str) and core:
        for k in MACHINE_KEYS[arch]:
            if ha.get(k) != core:
                e("CORE-AGREE", f"homeassistant.{k} is {ha.get(k)!r} but `core` is {core!r} "
                                f"(the Core version is written in two places and they differ)")
        if stem != "dev" and "qemuarm-64" in ha and ha["qemuarm-64"] != core:
            warns.append(f"WARN {MARKER} {name}: STALE-ARCH homeassistant.qemuarm-64 is {ha['qemuarm-64']!r}, "
                         f"the file offers Core {core!r} (value decision, not changed by this gate)")

    # ---- DIGEST-FORMAT / REQUIRED / IMAGE
    dg = d.get("digests")
    core_dg = None
    if dg is None:
        if stem in REQUIRED_IN:
            e("DIGEST-REQUIRED", f"no `digests` object; {name} needs digests.core.{arch}")
    elif not isinstance(dg, dict):
        e("DIGEST-FORMAT", "`digests` is not an object")
    else:
        core_dg = dg.get("core")
        if not isinstance(core_dg, dict):
            e("DIGEST-FORMAT" if "core" in dg else "DIGEST-REQUIRED",
              "`digests.core` is " + ("not an object" if "core" in dg else f"missing; {name} needs digests.core.{arch}"))
            core_dg = None
        else:
            for k, v in core_dg.items():
                if k not in CORE_IMAGE:
                    e("DIGEST-FORMAT", f"digests.core.{k}: unknown arch (want one of {sorted(CORE_IMAGE)})")
                elif not isinstance(v, str) or not DIGEST_RE.match(v):
                    e("DIGEST-FORMAT", f"digests.core.{k} {v!r} is not sha256:<64 lowercase hex>")
            if stem in REQUIRED_IN and arch not in core_dg:
                e("DIGEST-REQUIRED", f"digests.core.{arch} missing (have {sorted(core_dg)})")
            for k in core_dg:
                if k in CORE_IMAGE and k != arch:
                    e("DIGEST-IMAGE", f"digests.core.{k} in {name}, whose Core image is the {arch} one")
    if core_dg is not None:
        for sect in ("image", "images"):
            ci = (d.get(sect) or {}).get("core") if isinstance(d.get(sect), dict) else None
            if ci != CORE_IMAGE[arch]:
                e("DIGEST-IMAGE", f"{sect}.core is {ci!r}, a {arch} digest needs {CORE_IMAGE[arch]}")
    return errs, warns


# ------------------------------------------------------------------ online

class CannotCheck(Exception):
    pass


def _http(url, headers, retries=3):
    """GET with retries on transport errors and 5xx. Returns (status, headers, body)."""
    last = None
    for i in range(retries):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as ex:
            if ex.code < 500:
                return ex.code, ex.headers, ex.read()
            last = ex
        except (urllib.error.URLError, OSError) as ex:
            last = ex
        time.sleep(1 + i)
    raise CannotCheck(f"{url}: {last}")


def registry_token(base, repo):
    hdrs = {}
    user, pw = os.environ.get("GA_REGISTRY_USER"), os.environ.get("GA_REGISTRY_TOKEN")
    if user and pw:
        import base64
        hdrs["Authorization"] = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
    st, _, body = _http(f"{base}/token?service=ghcr.io&scope=repository:{repo}:pull", hdrs)
    if st != 200:
        raise CannotCheck(f"token for {repo}: HTTP {st}")
    try:
        return json.loads(body)["token"]
    except (ValueError, KeyError) as ex:
        raise CannotCheck(f"token for {repo}: unparseable ({ex})")


def check_registry(base, name, arch, tag, digest):
    """Online rules for one (file, arch). Returns findings."""
    errs = []
    M = f"{MARKER} {name}: "
    repo = CORE_IMAGE[arch].split("/", 1)[1]
    hdrs = {"Authorization": "Bearer " + registry_token(base, repo), "Accept": ACCEPT}
    st, h, body = _http(f"{base}/v2/{repo}/manifests/{tag}", hdrs)
    if st != 200:
        return [f"{M}DIGEST-REGISTRY {repo}:{tag} answers HTTP {st} (the pinned Core does not exist in the registry)"]
    real = "sha256:" + hashlib.sha256(body).hexdigest()
    if real != digest:
        errs.append(f"{M}DIGEST-REGISTRY digests.core.{arch} {digest} but {repo}:{tag} hashes to {real}")
    hdr = h.get("Docker-Content-Digest")
    if hdr and hdr != real:
        errs.append(f"{M}DIGEST-REGISTRY {repo}:{tag} body hashes to {real} but the registry header says {hdr}")
    st2, _, body2 = _http(f"{base}/v2/{repo}/manifests/{digest}", hdrs)
    if st2 != 200:
        errs.append(f"{M}DIGEST-BY-DIGEST {repo}@{digest} answers HTTP {st2} (the prefetch fetches by digest)")
    elif body2 != body and real == digest:
        errs.append(f"{M}DIGEST-BY-DIGEST {repo}@{digest} returns other bytes than {repo}:{tag}")
    try:
        m = json.loads(body)
    except ValueError:
        return errs + [f"{M}DIGEST-PREFETCHABLE manifest of {repo}:{tag} is not JSON"]
    if not isinstance(m, dict) or m.get("mediaType") not in MANIFEST_TYPES:
        errs.append(f"{M}DIGEST-PREFETCHABLE manifest mediaType {m.get('mediaType') if isinstance(m, dict) else m!r} "
                    f"is not a single image manifest (an index/list cannot be staged)")
        return errs
    cfg = m.get("config") or {}
    if cfg.get("mediaType") not in CONFIG_TYPES:
        errs.append(f"{M}DIGEST-PREFETCHABLE config mediaType {cfg.get('mediaType')!r}")
    layers = m.get("layers")
    if not isinstance(layers, list) or not layers:
        errs.append(f"{M}DIGEST-PREFETCHABLE manifest has no layers")
    else:
        bad = sorted({l.get("mediaType") for l in layers if l.get("mediaType") not in GZIP_LAYERS})
        if bad:
            errs.append(f"{M}DIGEST-PREFETCHABLE non-gzip layer types {bad} (ga_manager stages gzip layers only)")
    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(pathlib.Path(__file__).resolve().parent.parent))
    ap.add_argument("--registry", action="store_true", help="also compare every declared digest with the registry")
    ap.add_argument("--registry-url", default=DEFAULT_REGISTRY)
    a = ap.parse_args()
    d = pathlib.Path(a.root) / LINE_DIR
    files = sorted(f for f in d.glob("*.json") if f.stem in FILE_ARCH) if d.is_dir() else []
    if not files:
        print(f"{MARKER} no channel files under {d} (fail closed)")
        return 2
    errs, warns, online = [], [], 0
    for f in files:
        e, w = check_file(f, f.name)
        errs += e
        warns += w
    if a.registry:
        for f in files:
            try:
                doc = json.loads(f.read_text(encoding="utf-8"))
                arch = FILE_ARCH[f.stem]
                dig = doc["digests"]["core"][arch]
                tag = doc["core"]
            except (OSError, ValueError, KeyError, TypeError):
                continue  # offline rules already reported it (REQUIRED files) or it is optional and absent
            if not DIGEST_RE.match(str(dig)) or not isinstance(tag, str):
                continue
            try:
                errs += check_registry(a.registry_url.rstrip("/"), f.name, arch, tag, dig)
            except CannotCheck as ex:
                print(f"{MARKER} cannot reach the registry: {ex} (fail closed)")
                return 2
            online += 1
        if online == 0:
            print(f"{MARKER} --registry asked but no digest was compared (fail closed)")
            return 2
    for x in warns + errs:
        print(x)
    print(f"core digests: {len(files)} file(s) inspected, {online} compared with the registry, "
          f"{len(errs)} finding(s), {len(warns)} warning(s)")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
