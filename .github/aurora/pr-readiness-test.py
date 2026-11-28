#!/usr/bin/env python3
"""Unit tests for .github/aurora/pr-readiness.py."""
import importlib.util
import pathlib
import sys
from unittest.mock import patch

HERE = pathlib.Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("pr_readiness", HERE / "pr-readiness.py")
PRR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PRR)


def make_pr_ci(contexts):
    return {
        "url": "https://github.com/aurora-silicon/linux/pull/2772",
        "commits": {
            "nodes": [
                {
                    "commit": {
                        "committedDate": "2026-11-24T10:00:00Z",
                        "statusCheckRollup": {"contexts": {"nodes": contexts}},
                    }
                }
            ]
        },
    }


def check_run(name, status, conclusion, db_id=None, started=None, completed=None, url=None):
    node = {
        "__typename": "CheckRun",
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "detailsUrl": url or f"https://example.invalid/{name}/{db_id or 0}",
    }
    if db_id is not None:
        node["databaseId"] = db_id
    if started is not None:
        node["startedAt"] = started
    if completed is not None:
        node["completedAt"] = completed
    return node


def status_ctx(context, state, created=None):
    node = {
        "__typename": "StatusContext",
        "context": context,
        "state": state,
        "targetUrl": f"https://example.invalid/{context}",
    }
    if created is not None:
        node["createdAt"] = created
    return node


def test_ci_state_latest_run():
    # Issue #9: older CANCELLED run + newer SUCCESS run -> ok (regardless of list order)
    for order in (
        [
            check_run("apple-build", "COMPLETED", "CANCELLED", db_id=100, started="2026-11-24T10:00:00Z", completed="2026-11-24T10:01:00Z"),
            check_run("apple-build", "COMPLETED", "SUCCESS", db_id=200, started="2026-11-24T10:01:05Z", completed="2026-11-24T10:05:00Z"),
        ],
        [
            check_run("apple-build", "COMPLETED", "SUCCESS", db_id=200, started="2026-11-24T10:01:05Z", completed="2026-11-24T10:05:00Z"),
            check_run("apple-build", "COMPLETED", "CANCELLED", db_id=100, started="2026-11-24T10:00:00Z", completed="2026-11-24T10:01:00Z"),
        ],
    ):
        state, msg = PRR.ci_state(make_pr_ci(order))
        assert state == "ok", f"expected ok, got {(state, msg)}"
        assert "1 check passed" in msg, msg

    # Newer-failure control: older SUCCESS + newer FAILURE -> failing
    for order in (
        [
            check_run("apple-build", "COMPLETED", "SUCCESS", db_id=100, started="2026-11-24T10:00:00Z", completed="2026-11-24T10:02:00Z"),
            check_run("apple-build", "COMPLETED", "FAILURE", db_id=200, started="2026-11-24T10:03:00Z", completed="2026-11-24T10:04:00Z"),
        ],
        [
            check_run("apple-build", "COMPLETED", "FAILURE", db_id=200, started="2026-11-24T10:03:00Z", completed="2026-11-24T10:04:00Z"),
            check_run("apple-build", "COMPLETED", "SUCCESS", db_id=100, started="2026-11-24T10:00:00Z", completed="2026-11-24T10:02:00Z"),
        ],
    ):
        state, msg = PRR.ci_state(make_pr_ci(order))
        assert state == "failing", f"expected failing, got {(state, msg)}"
        assert "apple-build" in msg, msg

    # Newer QUEUED run (no timestamps yet) supersedes older CANCELLED via databaseId
    state, msg = PRR.ci_state(
        make_pr_ci(
            [
                check_run("apple-build", "COMPLETED", "CANCELLED", db_id=100, started="2026-11-24T10:00:00Z", completed="2026-11-24T10:01:00Z"),
                check_run("apple-build", "QUEUED", None, db_id=200),
            ]
        )
    )
    assert state == "pending", f"expected pending, got {(state, msg)}"

    # Sole CANCELLED run (no newer run) still fails
    state, msg = PRR.ci_state(
        make_pr_ci([check_run("apple-build", "COMPLETED", "CANCELLED", db_id=100)])
    )
    assert state == "failing", f"expected failing, got {(state, msg)}"

    # Timestamp fallback without databaseId
    state, msg = PRR.ci_state(
        make_pr_ci(
            [
                check_run("pr-text", "COMPLETED", "SUCCESS", started="2026-11-24T10:05:00Z", completed="2026-11-24T10:06:00Z"),
                check_run("pr-text", "COMPLETED", "CANCELLED", started="2026-11-24T10:00:00Z", completed="2026-11-24T10:01:00Z"),
            ]
        )
    )
    assert state == "ok", f"expected ok, got {(state, msg)}"

    # StatusContext deduplication by createdAt
    state, msg = PRR.ci_state(
        make_pr_ci(
            [
                status_ctx("ci/external", "FAILURE", created="2026-11-24T10:00:00Z"),
                status_ctx("ci/external", "SUCCESS", created="2026-11-24T10:05:00Z"),
            ]
        )
    )
    assert state == "ok", f"expected ok, got {(state, msg)}"

    # Different check names stay independent
    state, msg = PRR.ci_state(
        make_pr_ci(
            [
                check_run("apple-build", "COMPLETED", "SUCCESS", db_id=200),
                check_run("dt-checks", "COMPLETED", "FAILURE", db_id=150),
            ]
        )
    )
    assert state == "failing" and "dt-checks" in msg, (state, msg)


def test_hw_row_detection():
    # Issue #11: deferred / negated notes at the head SHA must NOT count as rows
    negatives = [
        (
            "aura#429 body",
            "746c4cd98c071111111111111111111111111111",
            "**Tested:** J715 M4 Air at aura main `58e712b9` + this change: MTP cold-started by Linux, keyboard and trackpad work, no DART faults. Not booted at `746c4cd9` itself.",
            [],
        ),
        (
            "aura#429 comment",
            "746c4cd98c071111111111111111111111111111",
            "**Tested:** see comments",
            ["The request for a J713 or desktop T8132 row is unchanged. A boot of `746c4cd98c07` would do."],
        ),
        (
            "linux#2672 body",
            "ad87173b11111111111111111111111111111111",
            "**Tested:** J715 M4 Air, 10 cores, OS firmware 26.6.2, with this entry: firmware identity accepted, READY, Hyprland session. Not booted from `ad87173b` itself.",
            [],
        ),
        (
            "linux#2745 dt-checks comment",
            "03884056556a1111111111111111111111111111",
            "**Tested:** see comments",
            ["About the `dt-checks` failure at `03884056556a` (2 new `CHECK_DTBS` warnings, 1 removed): both come from the schema, not from the change. The J604 has had the same warnings since it gained this property."],
        ),
        (
            "aura#539 compiles-only note",
            "3823d5e111111111111111111111111111111111",
            "**Tested:** J414s (M2 Pro) at `7443df8f`: 25 of 25 RTC-alarm sleeps woke. At this PR's head `3823d5e1` (rebased onto `8521ad7c`) the file compiles; it was not booted on a Mac.",
            [],
        ),
        (
            "linux#3503 queued refresh",
            "238f795dfba71111111111111111111111111111",
            "- **J773g (Mac mini M4, `T8132`, exact-head refresh on `238f795dfba7` queued)**",
            [],
        ),
        (
            "Not yet section excluded",
            "ed8a5a3734131111111111111111111111111111",
            "**Tested:** host tests only.\n**Risk:** two-way · M4\n**Not yet:**\n- J773g boot test at `ed8a5a373413`",
            [],
        ),
        (
            "single-sentence not tested",
            "deadbeef11111111111111111111111111111111",
            "**Tested:** J615 not tested at `deadbeef` yet.",
            [],
        ),
    ]
    for label, head, body, comments in negatives:
        pr = {
            "body": body,
            "comments": {"nodes": [{"body": c, "url": "https://example.invalid/c"} for c in comments]},
        }
        sec = PRR.fields(body)
        state, msg = PRR.find_row(pr, sec, head)
        assert state == "missing", f"{label}: expected missing, got {(state, msg)}"

    # Positive controls: real completed rows must still match
    positives = [
        (
            "standard Tested line",
            "04da8bbd11111111111111111111111111111111",
            "**Tested:** J616s T6040 at `04da8bbd` pass (0 failed units, 0 AVC).",
            [],
        ),
        (
            "multi-sentence single-line row (linux#3501 style)",
            "d237582111111111111111111111111111111111",
            "**Tested:** see comment",
            ["J616s (M4 Pro, DCP firmware 26.6.2) regression row, PASS. Lock-in PQ check, saved fleet89 vs 7.3.0-fw27rev (main 00fbe806 + d2375821): 0 DCP crashes."],
        ),
        (
            "skipped subtests count inside passing row",
            "5467f0f911111111111111111111111111111111",
            "**Tested:** J616s M4 Pro at `5467f0f9`: suspend-to-RAM 5/5 pass, 146/147 KUnit pass, 1 skipped, 0 failed units.",
            [],
        ),
        (
            "row in details Evidence block with separate Not yet",
            "9152b9ad11111111111111111111111111111111",
            "**Tested:** see Evidence\n**Not yet:** J713 boot at `9152b9ad`\n<details><summary>Evidence</summary>\n- J714s (T6050) row at head `9152b9ad`: pass.\n</details>",
            [],
        ),
        (
            "no runtime change exemption",
            "abcdef0111111111111111111111111111111111",
            "**Tested:** no runtime change (docs only).",
            [],
        ),
        (
            "exact-head verification heading",
            "bbfd2814c74a1111111111111111111111111111",
            "**Tested:** see comment",
            ["### Exact-head `bbfd2814c74a` verification (Apple M5 Max `J775c` / `G17C B1`):"],
        ),
    ]
    for label, head, body, comments in positives:
        pr = {
            "body": body,
            "comments": {"nodes": [{"body": c, "url": "https://example.invalid/c"} for c in comments]},
        }
        sec = PRR.fields(body)
        state, msg = PRR.find_row(pr, sec, head)
        assert state == "ok", f"{label}: expected ok, got {(state, msg)}"


HEAD = "deadbeef" + "1" * 32


def review(state="APPROVED", who="aurora-silicon-review[bot]", head=HEAD, when="2026-11-28T00:00:00Z", assoc="NONE"):
    return {
        "state": state, "author": {"login": who}, "authorAssociation": assoc,
        "commit": {"oid": head}, "submittedAt": when, "body": "",
        "url": "https://example.invalid/review",
    }


def review_pr(reviews):
    return {
        "author": {"login": "author"}, "reviews": {"nodes": reviews},
        "comments": {"nodes": []}, "reviewDecision": None,
        "latestOpinionatedReviews": {"nodes": []},
    }


def test_app_approval_from_reviews():
    pr = review_pr([review()])
    state, changes = PRR.review_state(pr, HEAD)
    assert state[0] == "ok" and changes[0] == "ok", (state, changes)
    # The evaluator no longer needs the connection that omits App reviews.
    del pr["latestOpinionatedReviews"]
    assert PRR.review_state(pr, HEAD)[0][0] == "ok"
    assert "latestOpinionatedReviews" not in PRR.DETAIL_Q
    # An App approval remains evidence, not a bypass for GitHub's ruleset.
    pr["reviewDecision"] = "REVIEW_REQUIRED"
    assert PRR.review_state(pr, HEAD)[0][0] == "missing"


def test_review_controls():
    for label, node in [
        ("stale approval", review(head="2" * 40)),
        ("self approval", review(who="author", assoc="OWNER")),
        ("untrusted App", review(who="other-app[bot]")),
        ("deleted reviewer", dict(review(), author=None)),
    ]:
        pr = review_pr([node])
        assert PRR.review_state(pr, HEAD)[0][0] == "missing", label
        if label != "untrusted App":
            pr["reviewDecision"] = "APPROVED"
            assert PRR.review_state(pr, HEAD)[0][0] == "missing", label
    pr = review_pr([review(who="collaborator", assoc="COLLABORATOR")])
    pr["reviewDecision"] = "APPROVED"
    assert PRR.review_state(pr, HEAD)[0][0] == "ok"
    for who, assoc in [("maintainer", "MEMBER"), ("owner", "OWNER"), ("aurora-silicon-review", "NONE")]:
        assert PRR.review_state(review_pr([review(who=who, assoc=assoc)]), HEAD)[0][0] == "ok", who
    # Newer non-opinionated reviews must not clear approval or a blocker.
    later = "2026-11-28T01:00:00Z"
    for state in ("COMMENTED", "PENDING"):
        pr = review_pr([review(state=state, when=later), review()])
        assert PRR.review_state(pr, HEAD)[0][0] == "ok", state
        pr["reviews"]["nodes"][1] = review(state="CHANGES_REQUESTED")
        assert PRR.review_state(pr, HEAD)[1][0] == "open", state
    for state in ("CHANGES_REQUESTED", "DISMISSED"):
        pr = review_pr([review(state=state, when=later), review()])
        approval, changes = PRR.review_state(pr, HEAD)
        assert approval[0] == "missing", (state, approval)
        assert changes[0] == ("open" if state == "CHANGES_REQUESTED" else "ok"), changes
        # The same reviewer's newer approval clears their old change request.
        pr["reviews"]["nodes"] = [review(when=later), review(state=state)]
        assert PRR.review_state(pr, HEAD)[1][0] == "ok", state
    # One reviewer's approval cannot clear another reviewer's blocker.
    pr = review_pr([review(), review(state="CHANGES_REQUESTED", who="maintainer", assoc="MEMBER")])
    assert PRR.review_state(pr, HEAD)[1][0] == "open"


def test_review_pagination():
    def page(nodes, cursor=None):
        return {"data": {"repository": {"pullRequest": {"reviews": {
            "nodes": nodes,
            "pageInfo": {"hasPreviousPage": cursor is not None, "startCursor": cursor},
        }}}}}

    replies = [
        page([review(state="COMMENTED", when="2026-11-28T02:00:00Z")], "middle"),
        page([review(state="COMMENTED", when="2026-11-28T01:00:00Z")], "oldest"),
        page([review()]),
    ]
    with patch.object(PRR, "request", side_effect=replies) as request:
        pr = PRR.graphql(PRR.DETAIL_Q, owner="example", name="repo", n=1)["repository"]["pullRequest"]
    assert len(pr["reviews"]["nodes"]) == 3
    assert [call.args[2]["variables"].get("before") for call in request.call_args_list] == [None, "middle", "oldest"]
    assert PRR.review_state(dict(review_pr([]), reviews=pr["reviews"]), HEAD)[0][0] == "ok"
    # A failed history read must stop evaluation rather than use partial data.
    with patch.object(PRR, "request", side_effect=[replies[0], SystemExit("review page unavailable")]):
        try:
            PRR.graphql(PRR.DETAIL_Q, owner="example", name="repo", n=1)
        except SystemExit as exc:
            assert str(exc) == "review page unavailable"
        else:
            raise AssertionError("partial review history was accepted")


def test_deferred_row_wording():
    for text in (
        "J616s at deadbeef: hardware row owed",
        "J616s at deadbeef: boot test owed",
        "J616s at deadbeef: row is still owed",
        "J616s at deadbeef: owed hardware row",
        "J616s at deadbeef: tests not run",
        "J616s at deadbeef: not yet run",
    ):
        assert not PRR.is_row(text, HEAD), text
    assert PRR.is_row("J616s at deadbeef: booted, 0 failed units", HEAD)


def test_counted_wrong_fixtures():
    # End-to-end evaluation: all three pieces of head evidence yield readiness.
    pr = dict(review_pr([review()]), **make_pr_ci([
        check_run("apple-build", "COMPLETED", "CANCELLED", db_id=100),
        check_run("apple-build", "COMPLETED", "SUCCESS", db_id=200),
    ]))
    pr.update({
        "headRefOid": HEAD, "number": 1, "baseRefName": "main",
        "createdAt": "2026-11-28T00:00:00Z", "mergeable": "MERGEABLE",
        "body": "**Tested:** J616s at deadbeef: booted, 0 failed units\n**Risk:** two-way · M4 Pro",
        "labels": {"nodes": []}, "files": {"nodes": []},
    })
    labels = PRR.evaluate(pr, [], "")[0]
    assert "ready-to-merge" in labels and "ci-failing" not in labels, labels
    print("PASS fixture #9: old cancellation + newer pass -> CI ok")
    print("PASS fixture App approval: empty latestOpinionatedReviews + exact-head APPROVED -> review ok")
    print("PASS fixture positive row: head SHA + J616s + completed result -> row ok")
    pr["body"] = pr["body"].replace("booted, 0 failed units", "hardware row owed; tests not run")
    labels = PRR.evaluate(pr, [], "")[0]
    assert "needs-hw-row" in labels and "ready-to-merge" not in labels, labels
    print("PASS fixture #11: head SHA + J616s + owed/not run -> needs-hw-row")


def main():
    tests = [
        test_ci_state_latest_run,
        test_hw_row_detection,
        test_deferred_row_wording,
        test_app_approval_from_reviews,
        test_review_controls,
        test_review_pagination,
        test_counted_wrong_fixtures,
    ]
    for fn in tests:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"ALL {len(tests)} PASSED")


if __name__ == "__main__":
    main()
