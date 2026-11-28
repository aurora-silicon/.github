# Governance

Aurora Silicon is run by its **Owners**, Ryan Murray ([@RyanTheTide](https://github.com/RyanTheTide)) and Dj ([@DjDeveloperr](https://github.com/DjDeveloperr)). They decide policy, cut releases, and own repository settings and rulesets.

| Role | Who | May |
| --- | --- | --- |
| Owner | Ryan, Dj | everything; alone cut `v*` releases and hotfixes, change settings, rulesets, secrets and access |
| Core | `core` team | review, approve and merge PRs that meet the merge policy on every product repo (the website: its designated owners); triage; run workflows; run the stable gate |
| Omarchy Mac maintainer | `omarchy-mac` team | review, approve and merge PRs that meet the merge policy on the repos Omarchy ships or consumes; triage; push `feat/` `fix/` `wip/` branches; run workflows |
| Aurora Maintainer | `aurora-maintainers` team | the same as Omarchy Mac, on the same repos: review, approve and merge PRs that meet the merge policy; triage; push `feat/` `fix/` `wip/` branches; run workflows |
| Aurora Dev | `aurora-dev` team | develop: push `feat/` `fix/` `wip/` branches, open PRs, review, re-run CI, request lab rows; **do not merge** into default branches (a PR needs a Core, Omarchy Mac or Aurora Maintainers approval before anyone can merge it; exceptions are listed in `access-tiers.md`) |
| Contributor | everyone | open issues and PRs from a fork |

Core, Omarchy Mac and Aurora Maintainers approvals are the ones that unlock a merge (code-owner review). Nobody merges their own PR without a non-author review, merges runtime code without a hardware row, pushes to a default branch, or moves a release tag. Which team holds which role on which repository: [`policy/access-tiers.md`](https://github.com/aurora-silicon/agent-guidelines/blob/main/policy/access-tiers.md). How to review and merge: the [handbook](https://github.com/aurora-silicon/agent-guidelines/blob/main/policy/review-and-merge-handbook.md).

The full text, including exactly what GitHub enforces and what is policy only, is [`policy/governance-and-roles.md`](https://github.com/aurora-silicon/agent-guidelines/blob/main/policy/governance-and-roles.md). The merge gate is [`policy/merge-policy.md`](https://github.com/aurora-silicon/agent-guidelines/blob/main/policy/merge-policy.md); branches and releases are [`policy/branching-and-releases.md`](https://github.com/aurora-silicon/agent-guidelines/blob/main/policy/branching-and-releases.md).

Policy changes: open a PR against [`aurora-silicon/agent-guidelines`](https://github.com/aurora-silicon/agent-guidelines). An Owner merges them.

## CODEOWNERS

GitHub has no organisation-wide CODEOWNERS file; each repository carries its own `.github/CODEOWNERS`. It does two jobs:

1. **Routing, and whose approval counts.** The first line, `* @aurora-silicon/core @aurora-silicon/omarchy-mac @aurora-silicon/aurora-maintainers` (only `@aurora-silicon/core` where Omarchy Mac and Aurora Maintainers have no role), requests a review from the teams that merge, and with "require review from code owners" on in the default-branch ruleset, any **one** approval from any of these teams satisfies it. Area lines may add reviewers who know the code (for example GPU paths), but always keep all the merging teams on the line so routing never turns into a two-person gate.
2. **Owner review for files that affect everyone.** Later, more specific lines (the last match wins) give `@RyanTheTide @DjDeveloperr` the paths that need an Owner: `.github/` (workflows, templates, CODEOWNERS itself), release and packaging workflows, the default build configuration (kernel: `arch/arm64/configs/aurora_defconfig`), `RELEASES.md`, `CONTRIBUTING.md` and other policy text.

Owners and Core bypass code-owner review only through a pull request, when the merge policy holds. Do not list a team or person who lacks write access: GitHub ignores them. `tools/gh-access-apply.sh` in agent-guidelines applies the rulesets; the CODEOWNERS file is added by a normal PR.
