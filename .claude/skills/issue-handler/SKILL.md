---
name: issue-handler
description: Own one TraceBench GitHub issue through a repository worktree, implementation, repository gates, PR review, CI, merge, and cleanup. Use when asked to handle, fix, close, or ship an issue in the TraceBench repository.
---

# Issue handler (TraceBench)

Own one issue through merge. Shipping means research, worktree, implementation, tests, validation, commit, push, PR, independent review, CI, merge, local main sync, and cleanup.

All implementation edits happen in a feature worktree. Never edit the main checkout for issue implementation.

## Preconditions

- The issue number or URL and target GitHub repository are known.
- `gh auth status` succeeds and the authenticated user has the required write access.
- The repository checkout at `$REPO_HOST` is on `main` and has a usable worktree host.
- No open PR already handles the issue. If one exists and you did not create it, attach to it only with user approval. Do not create a duplicate.
- A dirty checkout is not a reason to clean, stash, reset, or overwrite user work.

Stop and ask for missing permissions, an unclear target repository, contradictory acceptance criteria, a foreign worktree collision, or a product decision that the issue does not settle.

## Resolve the repository first

This skill serves one repository. Set explicit values and use them for every command:

```sh
GH_REPO="dayvidpham/TraceBench"
REPO_HOST="/home/minttea/codebases/dayvidpham/TraceBench"
BASE="main"
LIVE_REMOTE="origin"
ISSUE="<number>"
```

Use `gh ... -R "$GH_REPO"` for every issue, PR, comment, review, run, and merge operation.

## Workflow

1. **Orient** - Read the issue and all comments with `gh issue view "$ISSUE" -R "$GH_REPO"`. Read `AGENTS.md` and the testing or architecture documents it names.
2. **Research** - Map acceptance criteria to production paths, callers, tests, fixtures, generated outputs, and consumers. Check related issues and PRs. State observable acceptance bullets. Stop and ask if behavior remains ambiguous.
3. **Classify** - Select risk tier A, B, or C. Identify public API, release, migration, security, and interface-evidence requirements before editing.
4. **Worktree** - Fetch the base, create or resume the issue branch, and verify the worktree path and branch.
5. **Implement** - Make the smallest correct change. Preserve existing user-facing behavior outside the issue. Use production paths and real dependency wiring.
6. **Test** - Add or update tests for observable behavior. Put combinatorial and table-driven cases in `testdata/*.yaml` fixtures, not inline case tables.
7. **Validate** - Run the repository gate and all focused gates required by the changed surface. Never weaken or remove tests to make a gate pass.
8. **Evidence** - For a user-visible interface change (CLI output, file format, public API), capture the exact command and output from the branch. For a UI change, capture the mounted path in both themes. Otherwise write `not applicable` with a reason.
9. **Sync base** - If `main` moved or the branch was open for about an hour, fetch and merge the base into the feature branch. Regenerate generated artifacts instead of hand-merging them. Re-run the gate.
10. **Ship** - Inspect status, diff, and recent history. Stage intended files only. Commit with `git agent-commit`, push to `origin`, and open a focused PR against `main`.
11. **Review loop** - Run the required independent review wave on the current PR head. Post findings to the PR, fix blockers, and re-review every material new head.
12. **CI** - Watch all checks on the reviewed head. Fix branch-caused failures, re-run local gates, push, and repeat review when merge-bound files changed.
13. **Merge** - Merge only when the current head meets all review, CI, evidence, and repository gates.
14. **Cleanup** - Sync the local `main` checkout immediately, remove only the worktree and branch you created, delete the remote branch if it remains, and leave the current directory outside the deleted worktree.

## Risk tiers

The repository's instructions are the source of truth. Tiers add review and focused validation; they do not replace repository gates.

| Tier | Use when | Local and review bar |
|---|---|---|
| A | Documentation, skills, issue templates, or comments only | Relevant format/link checks; one reviewer optional unless process behavior changes |
| B | Normal code or test behavior | Full repository gate, focused tests, one three-axis review wave with zero blockers on current HEAD |
| C | Public API or file-format contract, concurrency, storage, security, migrations, release workflow, generated artifacts, or cross-repository behavior | Tier B plus focused integration/contract gates and at least one clean three-axis review wave on current HEAD |

Important and minor review findings do not block merge when repository policy permits. Record them as approved follow-up issues. Never hide, omit, or silently downgrade a finding.

## Repository scope

This is a single-repository skill. Do not expand scope into another repository on your own. If a change genuinely needs one, stop and ask the user first.

## Worktree setup

Use the repository root as the worktree owner. Create feature worktrees under `worktree/` in that root; never implement on `main`.

```sh
BRANCH="TraceBench-<issue>--<type>--<short-name>"
WT="$REPO_HOST/worktree/$BRANCH"

git -C "$REPO_HOST" fetch "$LIVE_REMOTE" "$BASE"
git -C "$REPO_HOST" worktree list

# Resume only if this exact worktree and branch are yours.
git -C "$REPO_HOST" worktree add -b "$BRANCH" "$WT" "$LIVE_REMOTE/$BASE"
```

The branch format is `<repo>-<issue>--<semantic-commit>--<description>`, for example `TraceBench-12--feat--corpus-loader`. The parent guide owns the convention.

Before every edit session, run these commands with the tool working directory set to `$WT`:

```sh
pwd
git branch --show-current
git status --short
```

`pwd` must be `$WT` and the branch must be `$BRANCH`. Stop on a foreign worktree or unexpected changes that conflict with the issue. Do not delete another agent's worktree.

## Repository gates

Read the current repository files before selecting commands. For Go changes, the baseline gate is:

```sh
gofmt -l .        # must print nothing
go vet ./...
go build ./...
go test -race ./...
go mod tidy       # must leave no diff
```

Once a `Makefile` exists, `make check` is the authoritative entry point and runs at least these steps. Additional gates when relevant: benchmark runs for performance-sensitive changes; focused `go test -race -run <pattern>` for the changed packages; regeneration plus a freshness check for generated files.

For task changes, validate the task as `README.md` documents, for example `harbor run -p tasks/<name> -a oracle -e docker` (use `-e podman` on macOS Docker Desktop). Keep the committed `task.toml` policy strict; relax it only on a scratch copy outside the repository.

Focused Go tests always use `-race`. Generated files are never hand-edited or hand-merged. Change the source and run the pinned generator. Confirm regeneration leaves no unexplained diff.

## Interface changes

When a change touches a user-visible surface (CLI output, file format, public API), the PR shows the exact command and its output, or the contract diff, and why the change is safe. Screenshots apply only when the repository has a user interface; otherwise write `not applicable` with a reason.

Before trusting evidence:

- Confirm the binary or server under test is the worktree build, not a stale artifact.
- Check the artifact for a marker introduced by the branch.
- Run the exact command a user would run.
- Inspect every artifact and the standing prior-finding checklist before upload.
- Keep local proof files untracked. Put durable evidence in the PR body or a linked PR comment.

## Commit and push

Inspect before committing:

```sh
git status --short
git diff
git log --oneline -10
```

Stage intended paths only. Do not stage unrelated user or agent changes.

```sh
git add <intended-paths>
git agent-commit -m "type(scope): concise summary"
git push -u "$LIVE_REMOTE" HEAD
```

Use the repository's conventional commit style. Do not include internal task-tracking terms (task IDs, slice, wave, phase, or epic names) in shipped code, docs, or commit messages. Do not amend, force-push, skip hooks, install hooks, or push secrets.

## Open the PR

```sh
gh pr create -R "$GH_REPO" --base "$BASE" --head "$BRANCH" \
  --title "type(scope): concise summary" --body-file <pr-body-file>
```

Add `--draft` when `Merge dependencies` says the PR is not safe to merge alone. Mark it ready (`gh pr ready`) only after every dependency has landed. A PR that is ready is always safe to merge on its own.

The PR body must contain:

```md
## Summary
- user-visible outcome
- important implementation boundary

## Issue
Fixes #<N>

## Merge dependencies
- must merge or release first: <PR or none>
- safe to merge alone now: yes | no (if no, the PR stays a draft until the dependency lands)
- breaks if merged too early: <what, or nothing>

## Umbrella
Part of <umbrella issue URL>, or none.

## Risk
Tier A | B | C, with reason

## Verification
- exact command and result
- exact command and result

## Interface evidence
- exact command output or contract diff, or `not applicable` with reason

## Compatibility impact
- public API, file format, or behavior change, or `none`
```

Call out migrations, compatibility changes, manual steps, skipped checks, and why any check was not available. Keep the body concise and factual.

## Independent review loop

Tier B and C changes require three fresh independent reviewers in one parallel wave:

- A: correctness and end-user behavior
- B: test quality, production-path coverage, fixtures, and non-vacuity
- C: simplicity, maintainability, boundaries, and missed reuse

Each reviewer prompt must include the exact GitHub repository, PR number or URL, base branch, current `headRefOid`, issue acceptance criteria, repository instructions, and the standing prior-finding checklist. Require the reviewer to:

- Run `gh pr diff <PR> -R <repo>` or inspect the full patch.
- Read surrounding callers, tests, fixtures, generated sources, and wiring.
- Report ranked findings with `path:line`, severity `BLOCKER`, `IMPORTANT`, or `MINOR`, a concrete failure scenario, and the reviewed SHA.
- Check shipped-artifact hygiene and fixture rules.
- Avoid impact-free style comments.
- Make no edits and return paste-ready findings.

Post every review result to the PR. Automated reviews use `COMMENT`, never self-approval or self-request-changes. Post findings verbatim; do not omit or downgrade them. If you dispute a blocker, post the dispute and stop for user direction.

A clean pass is also recorded on the PR and cites the exact reviewed SHA:

```md
## Review pass N (head `<sha>`) - clean
Axes A, B, and C: 0 blockers. Important and minor findings are resolved or linked to approved follow-up issues.
```

After any push that changes code, tests, fixtures, skills, workflows, generated artifacts, or user-visible docs, run a new review wave on the new head. A clean review of an older SHA does not count. Stop and ask if five waves do not reach zero blockers.

## Address findings

1. Pull all human and automated PR comments and review threads.
2. Fix every blocker. Fix important and minor findings when safe and in scope; otherwise create or link an approved follow-up issue and explain the deferral on the PR.
3. Add or adjust tests when behavior changes.
4. Re-run the full repository gate and focused checks.
5. Commit with `git agent-commit`, push a new commit, and reply with the commit SHA and result.
6. Run a fresh three-axis review wave on the new `headRefOid`.

## CI and merge gate

Watch CI with explicit repository identity:

```sh
gh pr checks <PR> -R "$GH_REPO" --watch
gh pr view <PR> -R "$GH_REPO" --json state,mergeable,mergeStateStatus,statusCheckRollup,reviewDecision,isDraft,headRefOid
```

Rerun an identified infrastructure-only failure at most twice. Fix branch-caused failures in the worktree. Stop after one bounded diagnosis when ownership is unclear.

Merge only when all conditions are true:

- The reviewed SHA equals the current PR head.
- The current-head review wave has zero blockers.
- Important and minor findings are fixed or linked to approved follow-up issues.
- All actionable human comments are addressed.
- `reviewDecision` is not `CHANGES_REQUESTED`.
- Required CI checks are green on the current head.
- Local repository and focused gates passed on that commit.
- Required interface evidence is durable and inspected.
- The PR is open, not draft, and mergeable.
- Every `Merge dependencies` item has landed.

Use the merge method required by the repository. TraceBench lands focused PRs as one squashed change. Never push release tags by hand.

## Hand-off to a human

When the PR must stop at ready-to-merge for a human instead of merging, finish every merge condition above first. Then post one PR comment titled `Why this needs a human review`, using a body file, with these parts:

1. **Why it is not agent-merged:** for example a user-facing interface, a migration, an irreversible release, or a repository rule that reserves the merge.
2. **The parts that deserve a second look:** each with file paths and one line on the risk or judgment call.
3. **What was already verified:** review waves, CI, evidence and mutation checks.
4. **Merge dependencies:** what must merge or release first, and whether the PR is safe to merge alone.

Report the comment URL with the PR. A hand-off without this comment is incomplete.

## Cleanup after merge

Verify the PR state and merge commit from GitHub, then sync the local `main` checkout immediately:

```sh
gh pr view <PR> -R "$GH_REPO" --json state,mergeCommit
git -C "$REPO_HOST" pull --ff-only "$LIVE_REMOTE" "$BASE"
git -C "$REPO_HOST" worktree remove "$WT"
git -C "$REPO_HOST" branch -d "$BRANCH"
git -C "$REPO_HOST" push "$LIVE_REMOTE" --delete "$BRANCH"
git -C "$REPO_HOST" worktree prune
```

Delete only the branch and worktree you created. If remote branch deletion is already complete, report that result and continue. Never leave the tool working directory inside a removed worktree.

## Hard rules

1. Resolve the GitHub repository, remote, base branch, and worktree host before any write.
2. Never implement an issue in the `main` checkout or on a foreign branch.
3. Never hand-edit generated files or place combinatorial test tables inline.
4. Never install or modify Git hooks without explicit user permission.
5. Never merge with blockers, failing checks, stale review SHA, missing interface evidence, incomplete local gates, or unmet merge dependencies.
6. Never omit or downgrade reviewer findings. Disputes require user direction.
7. Never force-push, use destructive reset, skip hooks, expose sensitive data, or weaken tests for green.
8. Always sync the local `main` checkout and clean up the feature worktree after merge.
9. Keep the umbrella issue current when the change is part of one.

## Stop and ask

- Authentication, repository write access, or branch protection prevents the workflow.
- The issue has an open PR owned by someone else.
- Acceptance criteria conflict or require a product choice.
- A change genuinely needs another repository.
- A foreign worktree or unexpected conflicting changes occupy the branch.
- A merge conflict cannot be resolved from repository rules and generated sources.
- CI remains red after two infrastructure reruns or ownership is ambiguous.
- A reviewer blocker is disputed or five review waves do not clear blockers.
- A release tag, secret, security policy, or GitHub setting would need a manual change not authorized by the user.

## What this skill is not

- Not permission to implement unrelated roadmap items or issue-epic siblings.
- Not a substitute for repository-local release, migration, or test instructions.
- Not a reason to maximize parallelism when dependencies or file conflicts require sequence.
