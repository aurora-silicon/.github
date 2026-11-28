# hwfact-lint

Rule (pre-pr-checklist.md): **no hardware fact as a literal; cite its source.** Hardware facts come from the machine: ALSA control info and TLV, PCM constraints, DT and ADT properties, the matched tuning, firmware replies.

`hwfact-lint` reads the lines a pull request adds and flags the ones that look like such a fact:

| Rule | Example | Read it from |
| --- | --- | --- |
| dB level | `0 dB`, `-41.8 dB` | the control's TLV |
| frequency | `100 Hz` | matched tuning, DT, codec data |
| sample rate | `48000`, `48_000`, `48000u32`, `48000.0_f32` | PCM hw_params constraints, tuning |
| hardware quantity | `amps: 2`, `const MAX_GAIN_DB: f32 = -6.0`, `#define AMP_CHANNELS 6` | ALSA info, DT/ADT, tuning |
| board or SoC id in a string | `"j313"`, `"apple,t8132"`, `"J616sAP"` | DT compatible, capability bits |

An assignment counts as a hardware quantity when its name has a word such as gain, volume, db, channels, amps, rate, ohms or temp (split on `_` and camelCase). Comments are skipped by file type; in C and DT sources a `#` line is a directive and is checked.

Docs, test paths (`tests/`, `test_*`, `*_test.rs`, KUnit) and lock files are skipped.

## Accepting a literal

Policy values (for example a safety margin the owner set) and interface constants are fine, as long as the code says so:

```
max_db = -6  # hwfact-ok: policy, safety margin set by the owner on 2026-11-27
```

Put the marker on the line itself, or in a comment on the line above. A marker with no reason (empty, or punctuation only) does not count. For a whole path, such as generated voicing assets, add it to `.hwfact-allow` at the repository root. The format is a glob, whitespace, then the reason:

```
firs/*/graph.json        voicing generated from the matched tuning; regenerated, not hand-edited
packaging/session/parity.json  qualification thresholds (policy), never fed to gain or ceilings
```

The check reads `.hwfact-allow` from the PR's base branch, so a PR cannot exempt itself.

## Rollout

The check is warn-first: findings are annotations and the check passes. A repository switches to failing with `with: { strict: true }` once its existing literals are either sourced or carry a reason. It is a heuristic, so treat a warning as a question for review, not proof. To opt in, copy `workflow-templates/hwfact-lint.yml` into the repository.
