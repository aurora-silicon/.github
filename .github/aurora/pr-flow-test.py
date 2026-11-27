#!/usr/bin/env python3
"""Offline tests for pr-flow's arm/merge row gate and lane holds.

  python3 .github/aurora/pr-flow-test.py
"""
import importlib.util
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
tmp = tempfile.mkdtemp()
lane = os.path.join(tmp, "PEER-ROWS.md")
cfg = {
    "org": "aurora-silicon", "state_dir": tmp, "regression_repos": ["linux", "mesa"],
    "hold_scan_files": [lane], "exclude_prs": [], "exclude_repos": [], "deny_logins": [], "no_merge_labels": [],
}
json.dump(cfg, open(os.path.join(tmp, "c.json"), "w"))
os.environ["PR_FLOW_CONFIG"] = os.path.join(tmp, "c.json")
spec = importlib.util.spec_from_file_location("pr_flow", os.path.join(HERE, "pr-flow.py"))
F = importlib.util.module_from_spec(spec)
spec.loader.exec_module(F)

HEAD = "027c9476464dc3f1a70b91501e94925df427d3a8"


def pr(body, comments=()):
    return {"body": body, "comments": {"nodes": [{"body": c} for c in comments]}}


cases = [
    # linux#3213 (merged without rows, reverted by #3351): builds + KUnit at the head, rows owed.
    ("linux#3213 rows owed", "linux", pr(
        "**What:** mark tunneled PCIe devices untrusted.\n"
        f"**Tested:** builds at {HEAD[:12]} (`W=1`, arm64); the KUnit `thunderbolt` suite: 78/78. Hardware rows still owed (below).\n"
        "**Risk:** two-way door · every Mac with USB4\n"
        "**Not yet:** hardware rows at this head on J714s (M5 Pro, USB4 dock) and J773g (M4 mini)."), False),
    # mesa#1168 (auto-merged 5 min before a failing Neo row, reverted by #1200): one Mac, more rows in Not yet.
    ("mesa#1168 one Mac + Not yet rows", "mesa", pr(
        f"**What:** descriptor fast path.\n**Tested:** J716c Apple M5 Max at `{HEAD[:11]}`: 212/212 unit tests PASS, 0 kernel errors.\n"
        "**Risk:** two-way door · all Macs\n**Not yet:** second-generation hardware rows on a Gen 9 machine (G17P or M3/M4)."), False),
    ("one Mac in a regression repo", "linux", pr(
        f"**What:** x.\n**Tested:** J413 M2 Air at {HEAD[:9]}: boots, 0 failed units.\n**Risk:** two-way · M2\n**Not yet:** nothing"), False),
    ("Still to run", "linux", pr(
        f"**What:** x.\n**Tested:** J413 M2 Air at {HEAD[:9]}: boots. J473 at {HEAD[:9]}: boots. Still to run: suspend.\n**Risk:** two-way · M2"), False),
    ("target + regression row", "linux", pr(
        f"**What:** x.\n**Tested:** J413 M2 Air at {HEAD[:9]}: boots, 0 failed units. J616s M4 Pro at {HEAD[:9]}: no regression, 0 failed units.\n"
        "**Risk:** two-way · M2\n**Not yet:** nothing"), True),
    ("regression row in a comment", "mesa", pr(
        f"**What:** x.\n**Tested:** J700 Neo at {HEAD[:9]}: deqp-vk 0 fail.\n**Risk:** two-way · Neo\n**Not yet:** nothing",
        [f"J473 M2 mini at {HEAD[:10]}: piglit no regression, 0 fail"]), True),
    ("one Mac outside regression repos", "speakerguardd", pr(
        f"**What:** x.\n**Tested:** J713 M4 Air at {HEAD[:9]}: tests pass.\n**Risk:** two-way · M4\n**Not yet:** nothing"), True),
    ("no runtime change", "linux", pr("**What:** docs.\n**Tested:** No runtime change.\n**Risk:** two-way · none\n**Not yet:** nothing"), True),
    ("row at an older head", "speakerguardd", pr(
        "**What:** x.\n**Tested:** J713 M4 Air at deadbeef1: tests pass.\n**Risk:** two-way · M4\n**Not yet:** nothing"), False),
]

fail = 0
for name, repo, p, want_ok in cases:
    why = F.strict_rows(p, repo, HEAD, set())
    ok = why == ""
    if ok != want_ok:
        fail += 1
        print(f"FAIL {name}: got {'armable' if ok else repr(why)}, want {'armable' if want_ok else 'blocked'}")
    else:
        print(f"ok   {name}" + (f" ({why})" if why else ""))

for name, revs, want in (
        ("approval at head", {"dj": {"state": "APPROVED", "oid": HEAD}}, True),
        ("approval before a forward-merge push (mesa#700)", {"satchlj": {"state": "APPROVED", "oid": "ce2b047c" + "0" * 32}}, False),
        ("changes requested at head", {"dj": {"state": "CHANGES_REQUESTED", "oid": HEAD}}, False)):
    got = F.approved_at_head({"head": HEAD, "reviews": revs})
    if got != want:
        fail += 1
        print(f"FAIL {name}: got {got}, want {want}")
    else:
        print(f"ok   {name}")

open(lane, "w").write(
    "- 15:28 PEER-ROWS: https://github.com/aurora-silicon/mesa/pull/1168 Neo J700 row: 12 FAIL, HOLD\n"
    "- 15:30 PEER-ROWS: mesa#680 Neo J700 PASS 213/213, 0 fail: no regressions\n"
    "- 15:31 PEER-ROWS: mesa#900 FAIL on J473\n"
    "- 15:40 PEER-ROWS: mesa#900 re-run PASS, Verdict: MERGE\n")
F.lane_holds()
for key, want in (("mesa#1168", True), ("mesa#680", False), ("mesa#900", False)):
    got = key in F.HOLDS
    if got != want:
        fail += 1
        print(f"FAIL hold {key}: got {got}, want {want}")
    else:
        print(f"ok   hold {key} = {got}")
sys.exit(1 if fail else 0)
