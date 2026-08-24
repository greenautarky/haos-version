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

if problems:
    print("FAIL: beta.json diverges from stable.json beyond the intended keys:")
    print("\n".join(problems))
    print("\nFix: sync beta.json to stable.json, keeping only the intended plugin flip.")
    sys.exit(1)

print(f"OK: beta.json differs from stable.json only in {sorted(ALLOWED)} "
      f"+ images{sorted(ALLOWED_IMAGES)}")
