<!-- Keep the visible part short. Detail for reviewers and agents goes in the folded blocks.
     No AI attribution, no serial numbers. Guide: https://github.com/aurora-silicon/agent-guidelines/blob/main/policy/pr-structure.md -->

**What:** <!-- one or two sentences: the change and why it's needed. Fixes #NNN -->

**Tested:** <!-- Macs at the head SHA + result, e.g. "J473 M2 mini, J413 M2 Air at `abc1234`: 0 failed units, deqp-vk 0 fail". Docs/CI only: "no runtime change" -->

**Risk:** <!-- one-way | two-way door · which Macs it can affect, e.g. "two-way · M4 Pro" -->

**Not yet:** <!-- what's still untested, or "nothing" -->

**Revert:** <!-- Revert procedure, including dependencies or state a revert cannot undo. -->

<details><summary>Before you request review</summary>

- [ ] Checked for overlapping work and linked dependencies or stacked base.
- [ ] Required CI is green at this head. The branch integrates cleanly with its base.
- [ ] Feature row on the target Mac at this head with production defaults. Kernel, DT, Mesa, aura and installer runtime changes also have boot and smoke rows on at least two affected families. For docs or CI-only changes with no runtime effect, say “no runtime change.”
- [ ] Exercised the failure paths that apply, including missing capabilities and existing-install upgrade or rollback. Checked the full diff against the [shared checklist](https://github.com/aurora-silicon/agent-guidelines/blob/main/pre-pr-checklist.md).

</details>

<details><summary>Details</summary>

<!-- How it works, design choices, anything a reviewer must know. -->

</details>

<details><summary>Evidence</summary>

<!-- Per row: Mac family/board, PR head SHA, booted kernel and Stage 2 hashes,
     test command and result, relevant dmesg excerpt and CI link.
     For a combined boot, link the shared receipt and show that the PR’s changed files are byte-identical.
     Smoke: display, input, networking, permitted audio sink, GPU render. Redact private identifiers. -->

</details>
