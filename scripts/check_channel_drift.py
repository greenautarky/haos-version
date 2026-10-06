#!/usr/bin/env python3
"""beta.json must differ from stable.json ONLY in the keys we intend.

Why this exists: a canary switched to `beta` stops testing what the fleet gets
the moment `stable.json` moves and `beta.json` does not. The divergence is
silent — the canary stays green while proving the wrong thing. This gate makes
the divergence loud, and it fails CLOSED: an unreadable or missing file is an
error, never a skip.

Intended divergences live in ALLOWED (top level) and ALLOWED_IMAGES. Everything
else must be byte-equal. Widening either list is a deliberate, reviewable act.
"""
import json
import os
import pathlib
import sys

ALLOWED = {
    "channel",
    "cli", "dns",      # GA-built plugin rebuilds (2026-08-24)
    "supervisor",      # widened 2026-08-24: beta is "the next fleet state",
                       # so it carries the Supervisor release under test
                       # (2025.11.4.7 = the Core-image-override fix, #706).
                       # This guard REFUSED the bump until the list was
                       # widened on purpose — which is the design.
}
ALLOWED_IMAGES = {"cli", "dns"}

def load(name):
    p = pathlib.Path(name)
    if not p.is_file():
        sys.exit(f"FAIL: {name} missing — cannot compare channels (fail closed)")
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError as e:
        sys.exit(f"FAIL: {name} is not valid JSON ({e}) — fail closed")

stable, beta = load("stable.json"), load("beta.json")


# --- promotion candidate (2026-10-06) ---------------------------------------
# A `candidate/*` branch carries the stable.json the NEXT release promotes:
# exactly what the dev canaries proved, under the stable label. The build of
# that release reads it from the candidate branch; no device ever polls a
# candidate branch (the Supervisor's version URL is compiled to `main`), and
# the fleet's stable.json on main moves only at promotion.
#
# The candidate state is declared by a file, PROMOTION_CANDIDATE, never
# inferred from a branch name: the guard must not change its verdict because a
# ref is spelled differently. While it exists:
#   * stable.json must equal dev.json in every key except `channel` and
#     `ga_release` — a candidate that is NOT what the canaries ran is the very
#     drift this file exists to stop, so this is stricter than the beta rule;
#   * stable.json `ga_release` must equal the release the marker names;
#   * beta.json is NOT compared with stable.json — during a candidate beta lags
#     behind stable by design, and the promotion PR realigns it;
#   * the marker must never reach main: on a push to main it is a failure. The
#     promotion PR deletes it, and the normal beta rule then applies again.
CANDIDATE_MARKER = pathlib.Path("PROMOTION_CANDIDATE")
CANDIDATE_MAY_DIFFER_FROM_DEV = {"channel", "ga_release"}

if CANDIDATE_MARKER.exists():
    cand = []
    if os.environ.get("GITHUB_REF") == "refs/heads/main":
        cand.append("  PROMOTION_CANDIDATE is on main. The promotion PR must delete it "
                    "and realign beta.json; a candidate marker on the branch the fleet "
                    "polls switches this guard's beta check off for the fleet")
    want_rel = ""
    for line in CANDIDATE_MARKER.read_text().splitlines():
        if line.startswith("release:"):
            want_rel = line.split(":", 1)[1].strip()
    if not want_rel:
        cand.append("  PROMOTION_CANDIDATE has no `release: <GA release>` line — fail closed")
    dev_c = load("dev.json")
    if stable.get("channel") != "stable":
        cand.append(f"  stable.json channel={stable.get('channel')!r}, must be 'stable'")
    if want_rel and stable.get("ga_release") != want_rel:
        cand.append(f"  stable.json ga_release={stable.get('ga_release')!r}, "
                    f"PROMOTION_CANDIDATE names {want_rel!r}")
    for key in sorted(set(stable) | set(dev_c)):
        if key in CANDIDATE_MAY_DIFFER_FROM_DEV:
            continue
        if stable.get(key) != dev_c.get(key):
            cand.append(f"  {key}: stable={stable.get(key)!r} dev={dev_c.get(key)!r}")
    if cand:
        print("FAIL: promotion candidate — stable.json is not the proven dev.json "
              "under the stable label:")
        print("\n".join(cand))
        sys.exit(1)
    print(f"OK: promotion candidate {want_rel}: stable.json == dev.json except "
          f"{sorted(CANDIDATE_MAY_DIFFER_FROM_DEV)}")
    print("NOTE: beta.json is NOT compared with stable.json while PROMOTION_CANDIDATE "
          "exists — the promotion PR deletes the marker and realigns beta")

problems = []
for key in sorted(set(stable) | set(beta)):
    if key in ALLOWED or key == "images":
        continue
    if stable.get(key) != beta.get(key):
        problems.append(f"  {key}: stable={stable.get(key)!r} beta={beta.get(key)!r}")

s_img, b_img = stable.get("images", {}), beta.get("images", {})
for key in sorted(set(s_img) | set(b_img)):
    if key in ALLOWED_IMAGES:
        continue
    if s_img.get(key) != b_img.get(key):
        problems.append(f"  images.{key}: stable={s_img.get(key)!r} beta={b_img.get(key)!r}")

# The intended divergences must ACTUALLY diverge — otherwise beta silently
# stopped being the plugin canary and nobody noticed.
inert = [k for k in ("cli", "dns") if stable.get(k) == beta.get(k)]
if inert:
    problems.append(
        f"  beta no longer diverges on {inert} — either the flip landed in stable "
        f"(then shrink ALLOWED) or beta drifted back; a beta channel that tests "
        f"nothing is worse than none")

if CANDIDATE_MARKER.exists():
    # Reported, not enforced: the candidate block above already held stable to
    # dev, which is the stronger rule while a promotion is pending.
    print(f"SKIPPED (promotion candidate): beta.json vs stable.json — "
          f"{len(problems)} divergence(s) the promotion PR must resolve")
elif problems:
    print("FAIL: beta.json diverges from stable.json beyond the intended keys:")
    print("\n".join(problems))
    print("\nFix: sync beta.json to stable.json, keeping only the intended plugin flip.")
    sys.exit(1)
else:
    print(f"OK: beta.json differs from stable.json only in {sorted(ALLOWED)} "
          f"+ images{sorted(ALLOWED_IMAGES)}")


# --- dev.json (2026-09-28): the canary-only channel for Core 2026.x ---------
# dev is "beta plus the change under proof". It may differ from beta only in
# the keys below; everything else must track beta, or a dev canary stops
# testing what beta would get. Fails closed if dev.json is missing.
# widened 2026-09-30: the rebuilt plugins (cli, dns) and the GA builds of
# audio, multicast and observer are the change under proof on the canaries;
# beta follows once a canary has run them.
DEV_ALLOWED = {"channel", "core", "homeassistant", "image", "supervisor",
               "cli", "dns", "audio", "multicast", "observer"}
DEV_ALLOWED_IMAGES = {"core", "audio", "multicast", "observer"}

dev = load("dev.json")
dev_problems = []
if dev.get("channel") != "dev":
    dev_problems.append(f"  channel: dev.json says {dev.get('channel')!r}, must be 'dev'")
for key in sorted(set(beta) | set(dev)):
    if key in DEV_ALLOWED or key == "images":
        continue
    if beta.get(key) != dev.get(key):
        dev_problems.append(f"  {key}: beta={beta.get(key)!r} dev={dev.get(key)!r}")
b_img, d_img = beta.get("images", {}), dev.get("images", {})
for key in sorted(set(b_img) | set(d_img)):
    if key in DEV_ALLOWED_IMAGES:
        continue
    if b_img.get(key) != d_img.get(key):
        dev_problems.append(f"  images.{key}: beta={b_img.get(key)!r} dev={d_img.get(key)!r}")

if dev_problems:
    print("FAIL: dev.json diverges from beta.json beyond the intended keys:")
    print("\n".join(dev_problems))
    sys.exit(1)

print(f"OK: dev.json differs from beta.json only in {sorted(DEV_ALLOWED)} "
      f"+ images{sorted(DEV_ALLOWED_IMAGES)}")


# --- internal consistency (2026-10-05) --------------------------------------
# Within ONE channel file the top-level `core` and the per-machine
# `homeassistant.default` / `homeassistant.tinker` describe the same Core and
# must agree. #18 raised the per-machine values to 2026.8.2.1 and left the
# top-level `core` on 2026.8.2; nothing compared them, and the OS build's
# XVER-06 caught it in the BOSv1.4.0-rc3 bake instead.
inner = []
for name in ("stable.json", "beta.json", "dev.json"):
    ch = load(name)
    top = ch.get("core")
    ha = ch.get("homeassistant", {})
    for k in ("default", "tinker"):
        if k in ha and ha[k] != top:
            inner.append(f"  {name}: core={top!r} but homeassistant.{k}={ha[k]!r}")
if inner:
    print("FAIL: a channel file disagrees with itself about the Core version:")
    print("\n".join(inner))
    sys.exit(1)
print("OK: every channel file names one Core version (core == homeassistant.default/tinker)")
