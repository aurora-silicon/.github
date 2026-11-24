# PR readiness bot

One comment and a set of labels on every open pull request, kept up to date by
[`.github/workflows/pr-readiness.yml`](../.github/workflows/pr-readiness.yml)
(logic: [`.github/aurora/pr-readiness.py`](../.github/aurora/pr-readiness.py)).
The rules it applies are the [merge policy](https://github.com/aurora-silicon/agent-guidelines/blob/main/policy/merge-policy.md)
and the [PR structure](https://github.com/aurora-silicon/agent-guidelines/blob/main/policy/pr-structure.md);
how maintainers use it is the [maintainer handbook](https://github.com/aurora-silicon/agent-guidelines/blob/main/docs/MAINTAINER-HANDBOOK.md).

## Labels

| Label | Set when |
| --- | --- |
| `ready-to-merge` | CI at the head passed (or the change has no CI), an approving review counts, a hardware row is at the head (or `No runtime change.`), no open change request, no conflict, no `hold` or `blocked`, and (for PRs opened from 2026-11-25) the body has its `Tested:` and `Risk:` lines. |
| `needs-review` | No approval that GitHub counts (Core or Omarchy Mac, not the author), and no `Verdict: MERGE` at the head from an organisation member other than the author. |
| `needs-hw-row` | No line in the PR body (folded blocks included) or a comment with the head SHA (7+ hex digits) and a Mac (`J616s`, `T6040`, `M2 Air`, ...), and `Tested:` does not say `no runtime change`. |
| `changes-requested` | A reviewer's latest review requests changes, or the latest review verdict at the head is FINISH, HOLD or CLOSE. |
| `ci-failing` | A check failed at the head. |
| `merge-conflict` | The PR conflicts with its base branch. |
| `needs-template` | The body lacks `Tested:` or `Risk:` (door and Macs). |
| `area:<name>` | A changed file matches a `# area: <name>` section of the repository's `CODEOWNERS`. |
| `blast:<m1..m5, neo, all, none>` | The Macs named on the `Risk:` line. |
| `door:one-way`, `door:two-way` | The door on the `Risk:` line. |

`hold` and `blocked` are set by people (blocked on purpose) and keep a PR out of `ready-to-merge`; the bot never sets or removes them or any other label. It removes `hw-row-ok` (a maintainer's
"I checked the row" override) when new commits arrive.

## When it runs

- On a push, a title/body/base edit, a `hold`/`blocked`/`hw-row-ok` label change, a review or a
  comment on a pull request (that pull request only). Other events are skipped without a runner,
  and a burst on one pull request collapses into one pending run.
- On a schedule for all open pull requests (linux and mesa every 30 minutes, other repositories
  every 3 hours at a minute picked from the repository name), incrementally: only pull requests whose head,
  CI state, mergeability, review decision, labels or update time changed are read again. A run
  works for at most 12 minutes; the rest continue in the next run.
- Drafts are skipped. Its comment is created when something happens on a pull request (or for
  pull requests opened from 2026-11-25), and edited in place only when it changes.

## Adding it to a repository

1. Copy [`workflow-templates/pr-readiness.yml`](../workflow-templates/pr-readiness.yml) and
   [`workflow-templates/pr-text.yml`](../workflow-templates/pr-text.yml) to the repository's
   `.github/workflows/` (or Actions, New workflow, "Aurora PR readiness" / "Aurora PR text").
2. Give `.github/CODEOWNERS` `# area: <name>` comment lines above each group of paths
   (see aurora-silicon/linux). Lines before the first `# area:` get no area label.
3. Nothing else: labels are created on first use.

## When it is wrong

Comment on the pull request what it got wrong, decide by the policy, and open an issue here with
the link. Run it by hand: Actions, pr-readiness, Run workflow, with the PR number.
