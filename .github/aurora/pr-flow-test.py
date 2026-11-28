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
    # Enough for an offline main() run (see run_main below).
    "our_accounts": ["RyanTheTide"], "app_logins": [], "caps": {}, "detail_budget_s": 600, "refresh_h": 6,
    "serial_repos": ["mesa"], "merges_per_repo_per_run": 1, "lanes_dir": tmp, "active_lanes_file": os.path.join(tmp, "none"),
    "not_owner_lanes": [], "reviewer_teams": [], "active_days": 7, "active_min_reviews": 1, "never_request": [],
    "reviewer_lanes": ["REVIEW6"], "lane_repos": {}, "human_caps": {}, "lane_caps": {}, "lane_lines_per_run": 5,
    "sla_hours": 4, "owners": {}, "branch_lanes": {}, "fallback_lane": "FIXUP-A", "metrics_every_h": 1,
    "queue_file": os.path.join(tmp, "QUEUE.md"), "outbox_every_h": 2, "outbox_file": os.path.join(tmp, "outbox.md"),
    "outbox_urls_per_person": 5,
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

# ---------------------------------------------------------------- holds across lane files
# REVIEW6 00:33: a later-scanned file's older neutral line must not hide a newer HOLD; only an explicit
# release newer than the hold clears it. Both scan orders.
lane_a, lane_b = os.path.join(tmp, "REVIEW6.md"), os.path.join(tmp, "MERGETRAIN6.md")


def holds_with(a_text, b_text, order, a_mtime=None):
    open(lane_a, "w").write(a_text)
    open(lane_b, "w").write(b_text)
    if a_mtime:
        os.utime(lane_a, (a_mtime, a_mtime))
    F.CFG["hold_scan_files"] = [lane_a, lane_b] if order == "ab" else [lane_b, lane_a]
    F.lane_holds()
    return dict(F.HOLDS)


MTIME = F.dt.datetime(2026, 11, 28, 12, 0, tzinfo=F.TZ).timestamp()
HOLD_LINE = "- 2026-11-28 00:20 AEDT REVIEW6 aura#369 hardware HOLD: the J473 row is at an older head\n"
for name, a_text, b_text, key, want, mtime in (
        ("newer HOLD vs older neutral line in another file", HOLD_LINE,
         "- 2026-11-27 23:50 AEDT MERGETRAIN6: aura#369 queued after linux#3368\n", "aura#369", True, None),
        ("newer neutral line in another file does not release", HOLD_LINE,
         "- 2026-11-28 00:40 AEDT MERGETRAIN6: aura#369 rebased cleanly\n", "aura#369", True, None),
        ("older explicit release in another file does not release", HOLD_LINE,
         "- 2026-11-28 00:10 AEDT MERGETRAIN6: aura#369 hold lifted\n", "aura#369", True, None),
        ("newer explicit release in another file releases", HOLD_LINE,
         "- 2026-11-28 00:45 AEDT MERGETRAIN6: aura#369 Verdict: MERGE at the new head\n", "aura#369", False, None),
        ("PASS row in another file does not release a review HOLD", HOLD_LINE,
         "- 2026-11-28 00:45 AEDT PEER-ROWS: aura#369 J473 PASS, 0 fail\n", "aura#369", True, None),
        ("HH:MM lines dated from the file: newer HOLD wins",
         "- 2026-11-28 09:00 AEDT REVIEW6 note\n- 10:05 REVIEW6 aura#370 HOLD: rows owed\n",
         "- 2026-11-28 09:00 AEDT MERGETRAIN6 note\n- 09:30 MERGETRAIN6: aura#370 Verdict: MERGE\n", "aura#370", True, None),
        ("undated file (times from mtime): newer release releases", "- 10:05 REVIEW6 aura#371 HOLD\n- 11:50 REVIEW6 other\n",
         "- 2026-11-28 11:00 AEDT MERGETRAIN6: aura#371 hold lifted\n", "aura#371", False, MTIME),
        ("undated file (times from mtime): older release does not", "- 10:05 REVIEW6 aura#371 HOLD\n- 11:50 REVIEW6 other\n",
         "- 2026-11-28 09:00 AEDT MERGETRAIN6: aura#371 hold lifted\n", "aura#371", True, MTIME),
        ("release line without its own time does not release across files", HOLD_LINE,
         "Merge-ready: aura#369\n", "aura#369", True, None)):
    for order in ("ab", "ba"):
        got = key in holds_with(a_text, b_text, order, mtime)
        if got != want:
            fail += 1
            print(f"FAIL hold across files [{order}] {name}: got {got}, want {want}")
        else:
            print(f"ok   hold across files [{order}] {name} = {got}")
F.CFG["hold_scan_files"] = [lane]

# ---------------------------------------------------------------- offline main() runs
# GitHub is replaced by a recorder: reads come from fixtures, every write is recorded, never sent.
OURS = "RyanTheTide"
NOW_ISO = F.NOW.isoformat()


def ev_of(repo, n, head, ready=True, mss="CLEAN", auto=False, auto_by=None, approved=True, created="2026-11-27T01:00:00Z"):
    return {
        "v": F.EV_VERSION, "rows_why": "", "manual": "", "repo": repo, "n": n,
        "url": f"https://github.com/aurora-silicon/{repo}/pull/{n}", "title": f"t{n}", "author": "satchlj", "ours": False,
        "member": True, "private": False, "head": head, "base": "main", "base_ok": True, "branch": f"b{n}",
        "created": created, "last_push": created, "ci": "ok", "ci_msg": "", "ready": ready, "need_review": not ready,
        "blockers": [], "excluded": "", "labels": [], "mss": mss, "decision": "APPROVED" if approved else "REVIEW_REQUIRED",
        "id": f"PR_{repo}_{n}", "auto": auto, "auto_by": auto_by, "requested": [], "teams": [], "req_at": {},
        "reviews": {"dj": {"state": "APPROVED", "at": created, "oid": head, "url": "u", "body": "Verdict: MERGE"}} if approved else {},
    }


def node_of(ev, updated="2026-11-27T02:00:00Z"):
    return {"number": ev["n"], "updatedAt": updated, "headRefOid": ev["head"], "isDraft": False, "mergeable": "MERGEABLE",
            "reviewDecision": ev["decision"], "createdAt": ev["created"],
            "repository": {"name": ev["repo"], "isPrivate": False, "isArchived": False}, "author": {"login": ev["author"], "__typename": "User"},
            "labels": {"nodes": []}, "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS"}}}]},
            "autoMergeRequest": {"enabledAt": NOW_ISO, "enabledBy": {"login": ev["auto_by"]}} if ev["auto"] else None,
            **({"author": {"login": "dependabot", "__typename": "Bot"}} if ev.get("bot") else {})}


def fp_of(node):
    roll = node["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["state"]
    return "|".join(str(x) for x in (node["headRefOid"], node["updatedAt"], roll, node["mergeable"], node["reviewDecision"], []))


def run_main(prs, detail_fails=False, budget=600):
    """prs: [(ev, cached_fp_matches, armed_by_flow)]. Returns the recorded writes."""
    writes = []
    listing, state = [], {"prs": {}}
    for ev, fresh, armed in prs:
        node = node_of(ev)
        listing.append(node)
        st = {"fp": fp_of(node) if fresh else "older-fingerprint", "ev": ev, "ev_at": NOW_ISO}
        if armed:
            st["armed"] = {"head": ev["head"], "at": NOW_ISO}
        state["prs"][f"{ev['repo']}#{ev['n']}"] = st
    open(os.path.join(tmp, "state.json"), "w").write(json.dumps(state))

    def fake_call(method, path, body=None, kind="read", tries=4):
        q = (body or {}).get("query", "")
        if path == "/graphql" and q.lstrip().startswith("mutation"):
            writes.append(("mutation", q.split("(")[1].split("{")[1].strip() if "{" in q else q, body["variables"].get("id")))
            return {"data": {}}
        if path == "/graphql" and q == F.LIST_Q:
            return {"data": {"search": {"pageInfo": {"hasNextPage": False}, "nodes": listing}}}
        if path == "/graphql" and q == F.DETAIL_Q:
            if detail_fails:
                raise RuntimeError("POST /graphql: HTTP 502: fixture")
            raise AssertionError("unexpected detail fetch")
        if path == "/graphql":
            return {"data": {"search": {"pageInfo": {"hasNextPage": False}, "nodes": [], "issueCount": 0}}}
        if method == "GET":
            return []
        writes.append((method, path, (body or {}).get("sha")))
        return {}

    def no_token(kind):
        raise AssertionError("test tried to reach GitHub")

    F.call, F.token = fake_call, no_token
    for g in ("counts", "ARMED_PER_REPO", "repo_actions", "LIVE_ARMS"):
        if isinstance(getattr(F, g, None), dict):
            getattr(F, g).clear()
    F.CFG["detail_budget_s"] = budget
    open(lane, "w").write("")
    argv, sys.argv = sys.argv, ["pr-flow"]
    try:
        F.main()
    finally:
        sys.argv = argv
    return writes


def merges(w):
    return [x[1] for x in w if x[0] == "PUT" and x[1].endswith("/merge")]


def arms(w):
    return [x[2] for x in w if x[0] == "mutation" and x[1].startswith("enablePullRequestAutoMerge")]


H1, H2, H3 = "a" * 40, "b" * 40, "c" * 40
main_cases = [
    # REVIEW6 00:21 blocker 1: a direct merge beside an outstanding same-repo arm.
    ("no direct merge beside our live mesa arm",
     [(ev_of("mesa", 10, H1, ready=False, mss="BLOCKED", auto=True, auto_by=OURS, approved=False), True, True),
      (ev_of("mesa", 11, H2, created="2026-11-27T02:00:00Z"), True, False)], {}, [], []),
    ("no direct merge beside our live arm (non-serial repo)",
     [(ev_of("linux", 20, H1, ready=False, mss="BLOCKED", auto=True, auto_by=OURS, approved=False), True, True),
      (ev_of("linux", 21, H2, created="2026-11-27T02:00:00Z"), True, False)], {}, [], []),
    ("no direct merge beside a live arm someone else set",
     [(ev_of("mesa", 12, H1, ready=False, mss="BLOCKED", auto=True, auto_by="dj", approved=False), True, False),
      (ev_of("mesa", 13, H2, created="2026-11-27T02:00:00Z"), True, False)], {}, [], []),
    ("arm first, then a merge candidate: arm only",
     [(ev_of("linux", 30, H1, ready=False, mss="BLOCKED", approved=False), True, False),
      (ev_of("linux", 31, H2, created="2026-11-27T02:00:00Z"), True, False)], {}, [], ["PR_linux_30"]),
    ("merge candidate first, then an arm: merge only",
     [(ev_of("linux", 41, H2), True, False),
      (ev_of("linux", 40, H1, ready=False, mss="BLOCKED", approved=False, created="2026-11-27T02:00:00Z"), True, False)],
     {}, ["/repos/aurora-silicon/linux/pulls/41/merge"], []),
    # REVIEW6 00:13 blocker 2: a failed refresh acts on the cached same-head evaluation.
    ("refresh error: cached same-head evaluation never merges",
     [(ev_of("speakerguardd", 50, H3), False, False)], {"detail_fails": True}, [], []),
    ("refresh error: cached evaluation never arms",
     [(ev_of("speakerguardd", 51, H3, ready=False, mss="BLOCKED", approved=False), False, False)], {"detail_fails": True}, [], []),
    ("detail budget spent: cached evaluation never merges",
     [(ev_of("speakerguardd", 52, H3), False, False)], {"budget": -1}, [], []),
    ("no direct merge beside a live arm on a bot PR (not in the evaluated list)",
     [(dict(ev_of("mesa", 14, H1, ready=False, mss="BLOCKED", auto=True, auto_by="dj", approved=False), bot=True), True, False),
      (ev_of("mesa", 15, H2, created="2026-11-27T02:00:00Z"), True, False)], {}, [], []),
    ("control: fresh evaluation merges",
     [(ev_of("speakerguardd", 53, H3), True, False)], {}, ["/repos/aurora-silicon/speakerguardd/pulls/53/merge"], []),
]
for name, prs, kw, want_merges, want_arms in main_cases:
    w = run_main(prs, **kw)
    got = (merges(w), arms(w))
    if got != (want_merges, want_arms):
        fail += 1
        print(f"FAIL main: {name}: merges {got[0]} arms {got[1]}, want {want_merges} {want_arms}")
    else:
        print(f"ok   main: {name}")
w = run_main([(ev_of("speakerguardd", 55, H3, ready=False, mss="BLOCKED", auto=True, auto_by=OURS, approved=False), False, True)],
             detail_fails=True)
if merges(w) or arms(w) or [x[2] for x in w if x[0] == "mutation" and x[1].startswith("disablePullRequestAutoMerge")] != ["PR_speakerguardd_55"]:
    fail += 1
    print(f"FAIL main: refresh error on a PR we armed: want only a disarm, got {w}")
else:
    print("ok   main: refresh error on a PR we armed: disarmed (fail closed), nothing else")
w = run_main([(ev_of("speakerguardd", 54, H3), False, False)], detail_fails=True)
if "speakerguardd#54" not in open(cfg["queue_file"]).read():
    fail += 1
    print("FAIL main: stale evaluation dropped from the queue")
else:
    print("ok   main: stale evaluation still listed in the queue")

print(f"{'FAIL' if fail else 'PASS'}: {fail} failing")
sys.exit(1 if fail else 0)
