# Wave brief — TraceBench PR <n>[, <n>]

## 1. What this wave is

A coordinated review of <one PR | two sibling PRs> by **one reviewer covering all axes**
(correctness, integration, minimal test sanity). The reviewer reviews every PR and votes
**ACCEPT** or **REVISE** per PR. The orchestrator curates the findings afterwards into a two-part
report: a short human document and a precise agent document.

**Lean mandate (user ruling 2026-10-04):** only leaks, broken pipeline/materialization paths, and
docs that would mislead agents are blockers. Testing findings are advisory; IMPORTANT/MINOR
findings route to follow-ups and do not block.

Scope boundaries to respect: <e.g. no routes, UI, migrations, dispatcher wiring; do not demand
them>. Do not propose <e.g. a scoring formula>. Review what is here against the acceptance.

## 2. Artifact(s)

| | PR <n> | PR <n> (if two) |
|---|---|---|
| URL | | |
| Issue | | |
| Head SHA | | |
| Merge base | | |
| Current default branch | | |
| Range | | |
| CI at SHA | | |

- Verify before trusting anything: `git -C <worktree> rev-parse HEAD` must equal the table's SHA,
  and `gh pr view <n> -R dayvidpham/TraceBench --json headRefOid` must agree. State the verified SHA
  at the top of your report.
- Full patches: `<base-dir>/pr<n>.patch`.
- <UI or not; evidence required or not.>
- <Prior review status: first review | prior findings listed in §6.>

## 3. Environment

- Reviewer: `<base-dir>/review/pr<n>` (detached at the head SHA), `<base-dir>/review/integration`
  scratch at the current default branch.
- <If the change needs a service, the reviewer's instance and connection details go here.>

The PR checkout is read-only. The integration checkout is disposable for local merge and generation
experiments: no commits, no pushes, reset it when done.

Spot-check recipe (the orchestrator already ran the authoritative checks; see section 3a).
Reproduce a specific claim only; do not repeat a suite the evidence already covers:

```sh
gofmt -l .
go vet ./...
go build ./...
go test -race ./...
```

<For a task change, the authoritative check is the task validation from README.md, for example
`harbor run -p tasks/<name> -a oracle -e docker` (or `-e podman` on macOS).>

## 3a. Validation evidence (already run by the orchestrator)

The orchestrator ran these at the exact reviewed SHA, once, before this wave started. They are
relevant to this change and up to date at this SHA, so cite them as the baseline. Spot-check a
specific claim when it is load-bearing; do not repeat a covered suite wholesale.

| Subject | Command or workflow | Observed result (load-bearing line) | Run URL | Wall time |
|---|---|---|---|---|
| <repository gate> | | | | |
| <exact-head CI> | | | | |
| <gate CI does not run (task validation)> | | | | |

Coverage limits: <what the evidence does not establish, for example container-bound task runs and
network-restricted sandboxes.>

## 4. Issue requirements and acceptance

**Problem.** <bullets>

**Scope.** <numbered bullets from the issue>

**Acceptance.** <verbatim bullets from the issue>

**Open decisions.** <rulings the issue leaves open, if any>

## 5. Epic and invariants

<The invariants and boundaries this PR must honor, with references.>

## 6. Prior review items (re-reviews only)

Every item from the previous review at the prior SHA must be confirmed or refuted against the
current build, with evidence. Do not re-review only the fix. A small, non-functional delta (docs,
message strings) may be verified with a focused fix-verification pass; state what was and was not
re-walked.

1. <prior finding> — <location, what to check>
2. …

## 7. Specific parity and verification pointers

- <The extraction or move to diff against its previous implementation.>
- <Callers that must behave identically.>
- <Tests or fixtures the acceptance names, and whether they exist.>
- <Generated output that must regenerate with zero diff.>

## 8. Repository rules

- Test cases live in named YAML fixtures with required-name manifests; closed sets assert exact
  membership; the subject is never mocked; focused Go tests use `-race`.
- Generated files are regenerated with the pinned tool, never hand-edited.
- Public API or file-format changes state their compatibility impact.
- No internal task taxonomy in code, docs, comments, or reports; public-audience prose.

## 9. Output contract for the reviewer

Write `<base-dir>/report.md` with:

1. **Verdict** at the verified SHA — `ACCEPT` or `REVISE` (binary).
2. **Problem statement** — grounded in the issue, in your own words.
3. **Constraints** — the binding rules that shaped the solution.
4. **Requirements** — mapped to where the code implements each.
5. **Acceptance criteria** — each issue bullet assessed met / partial / deferred with evidence.
6. **Developed solution** — what was built, key files, mechanism end to end.
7. **Tradeoffs** — decisions taken, alternatives not taken, costs.
8. **Findings** ranked `BLOCKER` / `IMPORTANT` / `MINOR`, each with `path:line`, a concrete
   scenario, impact, and a specific fix.
9. **An explanatory ASCII diagram** of the mechanism as built.
10. **At least one C4 diagram**: a Mermaid block per the `c4-model` skill. The brief may waive
    this for a lean wave. Lint any ASCII `c4` block with
    `python3 "$REPO_HOST/.claude/skills/c4-model/scripts/c4-lint.py" <report>` (exit 0 required).
11. **Checks run / skipped**: the exact commands and observed results, plus coverage limits; section
    3a already carries relevant, up-to-date results, so do not repeat a covered suite.
12. **Integration with the current default branch** — merge, generation, and relevant tests.

## 10. Reviewer constraints

- Read-only on the PR checkouts; scratch experiments only in your own integration checkout; no
  edits, commits, or pushes.
- No GitHub comments; the orchestrator consolidates and posts.
- Time-box by prioritizing the load-bearing checks; <skip list and why>.
