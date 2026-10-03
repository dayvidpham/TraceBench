---
name: issue-orchestrator
description: Drive approved TraceBench issues to merge through dependency-aware worktrees and issue-handler agents. Use when asked to orchestrate issues, farm a backlog, or coordinate multiple issue-to-merge lanes.
---

# Issue orchestrator (TraceBench)

Coordinate many approved GitHub issues in one repository. Do not implement production code in the main checkout. Each shippable issue is owned by an `issue-handler` in its own isolated feature worktree.

## When to use

- The user asks to orchestrate, farm, or drain approved GitHub issues.
- An epic or initiative has multiple children.
- Several independent issues can progress in parallel without violating file or dependency boundaries.

Use `issue-handler` directly for one issue. Use `epic-composer` before this skill when findings still need issue design and user approval.

## Repository identity

This skill serves one repository. Never use bare `gh` commands without repository identity.

```sh
GH_REPO="dayvidpham/TraceBench"
REPO_HOST="/home/minttea/codebases/dayvidpham/TraceBench"
BASE="main"
LIVE_REMOTE="origin"
```

## Source of truth

Refresh before each loop:

```sh
gh issue list -R "$GH_REPO" --state open --limit 100
gh pr list -R "$GH_REPO" --state open
gh issue view <N> -R "$GH_REPO"
git -C "$REPO_HOST" worktree list
git -C "$REPO_HOST" fetch "$LIVE_REMOTE" "$BASE"
```

Read issue sections such as `Blocked by / Blocks`, `Children`, `Related`, and acceptance criteria. GitHub issue references and current repository state are authoritative.

## Dependency rules

Build a directed graph from explicit issue evidence:

- `A blocked by B` means B must merge or close first.
- A migration or data-format change must precede work that depends on the new shape.
- Generated-artifact changes must precede work that consumes the regenerated output when the consumer cannot compile against the old output.
- An epic is not a shippable lane. Dispatch its approved child issues.
- An umbrella issue is a tracking record, not a lane. Keep its PR list and merge-dependency map current.

Do not infer a hard dependency from a `Related` link. Stop and ask when issue bodies disagree.

## Conflict rules

Do not run issues in parallel when they modify the same semantic production path, generated source, migration sequence, release ceremony, or core file. File overlap alone is not always a conflict, but shared behavior ownership is.

Common serialization points include:

- Generated files and their source.
- Migrations and storage schemas.
- Shared package APIs and their callers.
- Release configuration and workflow files.
- Fixture corpora that several changes would extend.

Use one semantic branch name per issue, and one isolated worktree per issue. The issue handler owns each issue's commit, PR, tests, review, merge, and cleanup.

## Ready set

An issue is ready only when all conditions are true:

- State is open and scope is approved.
- Every explicit blocker required before implementation is closed, or the issue explicitly permits coordinated pre-merge development.
- Every blocker required before merge has a known landing order.
- No open PR already implements the issue.
- No active lane owns a conflicting semantic path.
- Required repository permissions and local worktree host are available.
- The issue has enough acceptance detail for an issue handler to proceed without inventing product behavior.

Priority order:

1. User-selected issue or release-critical blocker.
2. Security, privacy, data-loss, broken production behavior, and failing baseline work.
3. Lowest-level dependency that unlocks the most approved work.
4. Repository priority in issue titles, `P1` before `P2` before `P3`.
5. Lowest issue number as a stable tie-breaker.

The default parallelism cap is four lanes. Lower it when gates are resource-heavy or semantic conflicts reduce safe concurrency.

## Dispatch

For each ready issue:

1. Spawn an issue-handler agent with the exact `owner/repo`, issue number or URL, base branch, live remote, known dependencies, acceptance criteria, and conflicting paths.
2. Require the handler to read repository instructions before creating its worktree.
3. Track `issue -> worktree -> branch -> PR -> head SHA -> review -> CI -> merge`.
4. Record merge dependencies on the PR and keep the umbrella issue's dependency map current.

Do not launch long-running supervisor or worker task subagents for a backlog. For a small approved set, issue-handler agents can run in parallel as separate lanes.

## Review coordination

Each code-changing PR must complete the issue-handler's three-axis review wave on its current head SHA. Batch completed work for a coordinated review wave when several related lanes finish together, but preserve findings and verdicts per PR.

Reviewers are fresh for each wave. Continuity lives in issue/PR records and the standing prior-finding checklist. Review agents must post or return paste-ready findings; the handler posts every finding verbatim to the correct PR.

Zero blockers permits merge when all other gates pass. Important and minor findings become approved follow-up issues instead of silently disappearing. Do not create a duplicate follow-up when an existing issue already owns the finding.

## Umbrella issue and dependency map

An initiative that needs more than one epic gets one umbrella issue in this repository. When the approved set needs one and it does not exist, create it, then keep it current:

- Every PR in the set, with its status and the review order.
- A merge-dependency map: an arrow for each "must merge or release first" relation, and a table of pull request, depends on, safe to merge alone now, and what breaks if merged too early.

Update it when a PR opens, becomes ready, or merges, and head the PR list with "statuses last checked <date>". Tell every handler to link the umbrella and state its merge dependencies in the PR body.

## Orchestrator loop

```text
loop:
  1. Refresh issues, PRs, checks, worktrees, and the live base.
  2. Rebuild the dependency and conflict graphs from current evidence.
  3. Fill safe slots with the highest-priority ready issues.
  4. Ensure each in-flight lane advances through implementation, review, CI, and merge.
  5. After a merge, verify main sync and worktree cleanup, and unlock dependents.
  6. On a blocker, record it on the issue or PR and free the lane if no useful work remains.
  7. Exit when the approved set is merged, the user stops, or only blocked work remains.
```

Do not poll background agents with sleeps. Continue independent work and use completion notifications.

## CI and stalls

| State | Action |
|---|---|
| Branch-caused CI failure | Return lane to its handler for fix, full local gate, push, and fresh review |
| Infrastructure-only failure | Rerun failed jobs at most twice, then stop and ask |
| Base moved | Handler merges the live base into its worktree and reruns gates |
| Generated conflict | Merge canonical source and regenerate; never hand-merge output |
| Product ambiguity | Record exact question and stop that lane |
| Five review waves with blockers | Stop and ask; do not merge |
| Merge dependency not landed | Keep the PR a draft; do not merge it early |

## Reporting

Lead with repository-qualified status:

```text
Ready: TraceBench#N, TraceBench#M
In flight: TraceBench#N -> PR <url> (review | CI | blocked)
Waiting: TraceBench#M blocked by TraceBench#N
Merged: TraceBench#N -> <merge SHA>; local main synced
Next: TraceBench#M because its dependency merged
```

Do not claim green, reviewed, merged, or synced without checking GitHub and the local `main` checkout.

## Cleanup and completion

After every merge, require the handler to:

- Verify GitHub merge state and merge commit.
- Pull the live base into the local `main` checkout immediately.
- Remove only its feature worktree.
- Delete only its local and remote feature branch.
- Prune worktree metadata.
- Leave the current directory outside the deleted worktree.

Before reporting the approved set complete, verify every issue is closed, every PR is merged, CI is green, required evidence is durable, and no handler worktree remains.

## Hard rules

1. Always qualify issue and PR numbers with the repository.
2. Never implement production code in the `main` checkout.
3. Never parallelize semantic conflicts or violate a known landing order.
4. Never duplicate an existing PR.
5. Never merge over blockers, stale reviews, failing checks, missing evidence, or incomplete repository gates.
6. Never install Git hooks, force-push, hand-merge generated files, or expose sensitive data.
7. Never turn unapproved review findings into silent scope changes. Use approved follow-up issues.
8. Always verify local `main` sync and feature-worktree cleanup after merge.
9. Keep umbrella issues current when one exists.

## Stop and ask

- Authentication, permissions, or remote identity is unclear.
- Issue dependencies conflict.
- Two lanes need the same semantic production path and order is not clear.
- A manual release tag, repository setting, secret, or production operation is required without explicit authorization.
- CI is still blocked after bounded infrastructure retries.
- A review blocker is disputed or persists after five waves.
- Only blocked issues remain or the requested issue set is not approved.

## What this skill is not

- Not an issue-writing or epic-design substitute; use `issue-create-and-handle` or `epic-composer` first.
- Not permission to implement every open issue in the repository.
- Not a reason to maximize agent count when dependencies, resources, or conflicts require sequence.
- Not a release operator for reserved public tag cuts.
