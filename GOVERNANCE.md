# Governance

Aurora Silicon is run by its **Owners**, Ryan Murray ([@RyanTheTide](https://github.com/RyanTheTide)) and Dj ([@DjDeveloperr](https://github.com/DjDeveloperr)). They decide policy, cut releases, and own repository settings and rulesets.

| Role | Who | May |
| --- | --- | --- |
| Owner | Ryan, Dj | everything; alone cut `v*` releases and hotfixes, change settings and rulesets, add or remove people |
| Core | `core` and `aurora-dev` teams | review, approve and merge PRs that meet the merge policy; push `feat/` `fix/` `wip/` branches; open `rebase/` PRs |
| Maintainer | `omarchy-m` team | review, approve and merge PRs that meet the merge policy; triage; push `feat/` `fix/` `wip/` branches; run workflows |
| Contributor | everyone | open issues and PRs from a fork |

Nobody merges their own PR without a non-author review, merges runtime code without a hardware row, pushes to `main`, or moves a release tag.

The full text, including exactly what GitHub enforces and what is policy only, is [`policy/governance-and-roles.md`](https://github.com/aurora-silicon/agent-guidelines/blob/main/policy/governance-and-roles.md). The merge gate is [`policy/merge-policy.md`](https://github.com/aurora-silicon/agent-guidelines/blob/main/policy/merge-policy.md); branches and releases are [`policy/branching-and-releases.md`](https://github.com/aurora-silicon/agent-guidelines/blob/main/policy/branching-and-releases.md).

Policy changes: open a PR against [`aurora-silicon/agent-guidelines`](https://github.com/aurora-silicon/agent-guidelines). An Owner merges them.

## CODEOWNERS

GitHub has no organisation-wide CODEOWNERS file; each repository carries its own `.github/CODEOWNERS`. Use it only for files whose change affects everyone, so that day-to-day code review is not gated on two people:

- `.github/` (workflows, templates, CODEOWNERS itself), release and packaging workflows;
- the default build configuration (for the kernel: `arch/arm64/configs/aurora_defconfig`);
- `RELEASES.md`, `CONTRIBUTING.md` and other policy text in the repo.

List the Owners for those paths (`@RyanTheTide @DjDeveloperr`) and, where a repo has an area owner, add them for their area. Turn on "require review from code owners" in the repo's `main` ruleset; Owners bypass it through a pull request, so their own merges are not blocked. Do not list a team or person who lacks write access: GitHub ignores them.
