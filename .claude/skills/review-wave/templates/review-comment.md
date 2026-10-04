# <Repo> PR <n> — review

Reviewed at commit `<head-sha>` (base `<merge-base>`; current `<default-branch>` `<base-head>`).
Verdict: **<ready | changes requested>** — <n> blocker(s), <n> important, <n> minor. One independent review covering all axes (correctness, integration, test sanity); <n> requested changes. <Scope: backend-only/frontend, screenshots required or not.>

---

# Part 1 — for humans

## Problem statement

- <What the PR is for, and why it matters, in a few bullets.>

## Findings and proposed fixes

| # | Severity | Finding | Proposed fix |
|---|---|---|---|
| B1 | Blocker | <one sentence> | <one sentence> |
| I1 | Important | | |
| M1 | Minor | | |

## <Mechanism diagram title>

```text
<the flow as built>
```

## <Blocker diagram title>

```text
<the defect, and how the proposed fix resolves it>
```

## Tradeoffs

- <decision taken — what it costs>
- <alternative — when it would win>

## Glossary

- **<term>** — <one line, using codebase or industry-standard vocabulary.>

---

# Part 2 — for agents

## Artifact

| | |
|---|---|
| PR | `<repo>#<n>`, branch `<head-branch>` |
| Issue / epic | `<issue>` / `<epic>` |
| Head | `<head-sha>` |
| Merge base | `<merge-base>`; current `<default-branch>` `<base-head>` (<delta and overlap>) |
| CI | `<status at head>` |
| Diff | `<n files, +x/−y, scope>` |
| Verdict | <changes requested — B<n> blocker, I<n> important, M<n> minor> |
| Reviewer verdict | <accept / revise> |

## Acceptance status

| Acceptance criterion | Status | Evidence |
|---|---|---|
| <issue bullet> | <met / partial / deferred> | `<path:line>`, test name, command |

## Findings

### B1 — blocker: <title>

- **Location:** `<path:line>`.
- **Symptom:** <what the code does that it should not, or does not do that it should.>
- **Evidence / reproduction:** <how it was verified; quote the external contract if one applies.>
- **Impact:** <what goes wrong, for whom, when.>
- **Required change:** <the specific fix.>
- **Tests to add:** <the cases that pin the fix.>
- **Alternatives:** <options with their tradeoffs.>

<repeat per finding: I1…, M1…>

## Verification run

- <exact command> — <observed result>.
- <exact command> — <observed result>.
- Not run: <what and why; CI coverage if applicable>.

## Integration with the current default branch

<Merge result, generation zero-diff, relevant tests, and the landing order for sibling PRs.>

## C4 model

```c4
<diagram; lint with the repository c4-model script until exit 0>
```
