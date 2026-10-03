Operating mechanics behind SKILL.md: isolation, reviewer prompts, curation, posting, housekeeping. Kept out of the skill body; SKILL.md carries the requirements.

# Review wave (TraceBench)

Run an independent, evidence-based review of one or two sibling pull requests and produce a
curated report the author can act on. The reviewers stop at findings; the orchestrator verifies
the load-bearing ones, decides what is worth keeping, and writes the public report.

This skill is the orchestrator side of the review. The reviewer side is the `/reviewer` skill and
the `reviewer` subagent. It works for planned waves and ad-hoc PR review.

## What a wave produces

- A verdict per PR: `ready` or `changes requested`, with severity-ranked findings
  (`BLOCKER` / `IMPORTANT` / `MINOR`).
- A curated report posted to the PR as one comment with two documents: a short human part first,
  then a precise agent part.
- Raw reviewer reports kept outside the repository (for example under `/tmp/opencode/<topic>/`).

## Inputs and preconditions

- One or two PR numbers or URLs; for two PRs they should be siblings in one feature area (they
  share migrations, docs, or boundaries worth reviewing together).
- `gh auth status` succeeds; the user can write comments on the PRs.
- No open PR owned by someone else is being reviewed without user approval.

Resolve the repository before any command. Never rely on a bare PR number.

```sh
GH_REPO="dayvidpham/TraceBench"
PR=<number>
LIVE_REMOTE="origin"
REPO_HOST="/home/minttea/codebases/dayvidpham/TraceBench"
```

## Phase 0 — Recon

Read the PR, its issue, and any epic or umbrella it belongs to. Verify the head locally and on
GitHub. Check the base, the current default branch, and prior review records.

```sh
gh pr view "$PR" -R "$GH_REPO" --json title,author,state,isDraft,body,baseRefName,headRefOid, \
  mergeable,mergeStateStatus,additions,deletions,files,comments,statusCheckRollup
gh issue view <issue> -R "$GH_REPO" --json title,body,comments
git -C "$REPO_HOST" fetch "$LIVE_REMOTE" --quiet
BASE_SHA=$(git -C "$REPO_HOST" rev-parse "$LIVE_REMOTE/main")
git -C "$REPO_HOST" merge-base "$BASE_SHA" <head-sha>
git -C "$REPO_HOST" diff --shortstat <merge-base>..<head-sha>
git -C "$REPO_HOST" diff --name-only <merge-base>..<head-sha>
git -C "$REPO_HOST" diff --name-only <merge-base>.."$BASE_SHA"   # default-branch delta and overlap
```

Collect before designing the wave:

- The exact head SHA from `headRefOid`; record it and never review a different one.
- The merge base and the current default branch. If the default branch moved, note the delta and
  any file overlap with the PR; the reviewers need it for the integration check.
- CI status at the head (`statusCheckRollup`) and any known environment limitations the author
  reported.
- The issue's scope and acceptance bullets, plus any epic or umbrella invariants the PR must honor.
- Prior review records: `gh pr view "$PR" -R "$GH_REPO" --json comments,reviews`. If the PR was
  reviewed before, the prior findings become the standing checklist every reviewer must re-walk
  against the current SHA. Never re-review only the fix.
- External constraints the review will depend on: if a finding hinges on a vendor or protocol
  contract (API caps, pagination limits, error semantics), fetch the current documentation and
  quote it. Do not rely on memory.

## Phase 1 — Isolate

One PR checkout per reviewer, detached at the exact head SHA, plus one disposable integration
checkout at the current default branch. For a two-PR wave, create one checkout per PR per reviewer
(`pr<n>` and `pr<m>`). PR checkouts stay pristine; scratch work happens only in the integration
checkout.

```sh
BASE_DIR=/tmp/opencode/<topic>
for X in a b c; do
  mkdir -p "$BASE_DIR/$X"
  git -C "$REPO_HOST" worktree add --detach "$BASE_DIR/$X/pr<number>" <head-sha>
  git -C "$REPO_HOST" worktree add -b review-<pr>-<X>-integration \
    "$BASE_DIR/$X/integration" "$BASE_SHA"
done
gh pr diff "$PR" -R "$GH_REPO" > "$BASE_DIR/pr<number>.patch"
```

If the change needs a service (database, broker, object store), give each reviewer its own
isolated instance with an empty base state. Integration tests run their own migrations; never
pre-migrate the base, and reset it before each full-package run. Record the connection details in
the brief.

## Phase 2 — Write the wave brief

Write one brief file at `$BASE_DIR/wave-brief.md` from `templates/wave-brief.md`. It carries
everything the reviewers need so they never guess: artifact table with exact SHAs, environment
details, issue requirements and acceptance, epic invariants, the prior checklist for re-reviews,
specific parity or verification pointers, repository rules, and the output contract.

Rules for the brief:

- State the reviewed SHA and how to verify it (`git rev-parse HEAD`; `gh pr view --json headRefOid`).
- Quote the acceptance bullets; do not summarize them away.
- State scope boundaries so reviewers do not demand out-of-scope work (routes, UI, migrations,
  scoring formulas, and so on).
- Point at areas worth verifying; do not seed findings. The reviewers must find them.
- Keep the reviewers' own constraints in the brief: read-only checkouts, scratch only in the
  integration checkout, no GitHub writes, no pushes, time-box.

## Phase 3 — Spawn reviewers

Run three `reviewer` subagents in **one parallel batch**, one per lens. Each reviewer covers every PR in the wave:

- **Correctness** — does the implementation faithfully serve the issue and its invariants; are
  there gaps, regressions, or claims the code does not support?
- **API design** — is the surface no larger than the problem; boundaries, naming, contracts,
  over- and under-engineering.
- **Test quality** — fixtures with required names, exact membership, strict decoding, real
  services instead of mocking the subject, observable assertions, `-race`.

Each prompt starts with `/reviewer`, names its lens, points at `$BASE_DIR/wave-brief.md`, and
gives: its worktree, the report path it must write, and these constraints:

- Verify the SHA before reviewing anything.
- Write the report with bash heredocs (`cat > … <<'EOF'`); the `edit` tool is denied to reviewers.
- Produce a verdict (`ACCEPT` / `REVISE`), problem statement, constraints, requirements,
  acceptance status, developed solution, tradeoffs, ranked findings with `path:line`, a concrete
  scenario, impact and a fix, an explanatory ASCII diagram, at least one `c4` diagram, checks run
  and skipped, and the integration result against the current default branch.
- Lint every `c4` block with
  `python3 "$REPO_HOST/.claude/skills/c4-model/scripts/c4-lint.py" <report>`
  until it exits 0.
- Public-audience prose: no internal task IDs, slice or phase names, or workflow taxonomy.
- No edits, commits, pushes, or GitHub comments; return findings only.
- Never hand a subagent a bare PR number as its only target; give the SHA and local paths.

Reviewers are foreground teammates: spawn them together and wait for all three before curating.

## Phase 4 — Curate the findings

Read all three reports in full. Treat them as evidence, not verdicts. For every finding that would
change the report's verdict or a proposed fix:

- Reproduce or verify it yourself when it is cheap: read the code the finding cites, run the test,
  replay the interleaving, fetch the vendor documentation.
- Check it against the issue and the repository rules; a finding can be real code behavior and
  still be out of scope or contradicted by the acceptance.
- Keep only findings with real impact: a defect, a missed acceptance item, a contract or
  validation gap, or documentation that would mislead. Drop impact-free style or process
  comments.
- Decide one disposition per finding and record it: keep, downgrade, or drop, with the reason.
  Note disagreements between reviewers and how they were resolved.
- Fold duplicates from different reviewers into one finding with the best evidence.
- Keep the severity rubric: `BLOCKER` (wrong or harmful results on the production path, security,
  broken gates), `IMPORTANT` (missing validation or coverage at a boundary, contract problems),
  `MINOR` (hygiene, clarity, optional hardening).
- One-line documentation fixes can be the most valuable findings when the current text would
  mislead the next agent or operator, or when the corrected text prevents a whole class of wrong
  assumptions. Treat them seriously, and say why.
- Merge policy is zero blockers; important and minor findings route to follow-ups. Do not silently
  downgrade a blocker — if you disagree with a reviewer, record the evidence and surface the
  dispute.

## Phase 5 — Write the two-part report

Write one file, `$BASE_DIR/review-<pr>.md`, and show the user its absolute path. The file becomes
one PR comment with two documents, human part first, agent part second.

**Part 1 — for humans.** Short bullets, no walls of text.

- Problem statement: what the PR is for and why it matters, in a few bullets.
- Findings and proposed fixes: **exactly one table** with one row per finding.
- A few ASCII diagrams that show the mechanism and the main defect and its fix.
- Tradeoffs as bullets.
- Glossary at the end: the codebase and domain terms a reader needs.

**Part 2 — for agents.** Precise and unambiguous.

- Artifact and provenance: PR, issue, exact head SHA, merge base, current default branch, diff
  size, CI status, verdict.
- Acceptance status: one row per acceptance bullet with status and `path:line` evidence; tables are
  allowed here.
- One section per finding: location, symptom, reproduction or evidence, impact, required change,
  tests to add, alternatives with tradeoffs, and a small ASCII diagram of the fix when it is not
  obvious from the description (a blocker always gets one).
- Verification run: exact commands and observed results, plus what was intentionally skipped.
- One `c4` diagram (linted) and the integration result against the current default branch.

Terminology rules for both parts: use industry-standard terms or terms already in the codebase.
Do not invent vocabulary; describe behavior instead (for example "returns the complete list or an
error", not a coined name for it). No internal task IDs, slice/phase names, or workflow taxonomy
in posted text. Keep the whole comment under GitHub's 65,536-character limit.

## Phase 6 — Post and record

- Post with `gh pr comment <pr> -R "$GH_REPO" --body-file "$BASE_DIR/review-<pr>.md"`. Post only
  with user approval unless the user has already told you to post.
- Verify the published body: fetch it back and check its length, first line, and that both parts
  are present.
- Keep the wave record under `$BASE_DIR`: the brief, the three reviewer reports, and the posted
  comment URL.
- When the PR later gets a new head, that head needs a new wave; a clean review of an older SHA
  does not carry over, and the re-review walks the entire prior checklist again.

## Phase 7 — Housekeeping

- Leave PR checkouts pristine; reset the integration checkouts (`git reset --hard <sha>`).
- Keep raw reports and the brief under `/tmp/opencode/<topic>/`; never commit review evidence.
- Remove the review worktrees and branches you created after the PR lands; never remove another
  agent's worktree.
- After the reviewed PRs merge, the wave is closed: no follow-up work is owed unless the user
  asks; leave the findings record in place and update it only on request.
- Reset any service instances before a new full-package run.

## Hard rules

1. Verify the head SHA locally and on GitHub before any review statement; a stale SHA invalidates
   the wave.
2. Reviewers make no edits, commits, pushes, or GitHub posts; they return findings.
3. Never hand a subagent a bare PR number as its only target.
4. Never accept reviewer findings as gospel: reproduce load-bearing claims and record every
   keep/downgrade/drop decision with its reason.
5. Never demand out-of-scope work; check each finding against the issue and the repository rules.
6. Never silently downgrade a blocker; a disputed blocker goes to the user with the evidence.
7. Public text uses industry-standard or codebase terms only — no invented vocabulary and no
   internal task taxonomy.
8. Generated files are never hand-merged; fixture and manifest rules apply to any proposed fix.
9. Post to the PR only with user approval unless the user already said to post.
10. A new head SHA means a new wave; re-reviews walk the entire prior checklist.

## Stop and ask

- The head SHA changes mid-wave; restart on the new SHA.
- Reviewers disagree on a blocker you cannot settle with evidence.
- A finding depends on external behavior you cannot verify (vendor API, production config).
- The issue leaves a product or scope decision open that the review cannot resolve.
- The landing order between two sibling PRs is ambiguous after the merge simulation.

## Files in this skill

- `templates/wave-brief.md` — the brief the reviewers work from.
- `templates/review-comment.md` — the two-part report skeleton.
