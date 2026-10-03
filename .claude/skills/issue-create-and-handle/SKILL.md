---
name: issue-create-and-handle
description: Refine a TraceBench request into one repository-specific GitHub issue, obtain explicit approval, create and assign it, then hand it to issue-handler. Use when asked to file an issue and start work on it.
---

# Create, approve, assign, and handle a TraceBench issue

Convert the user's request into one scoped issue in the TraceBench repository. Get explicit approval of the exact title and body before creation. Then assign the issue to the authenticated GitHub user and immediately follow `issue-handler` through merge.

## Preconditions

- `gh auth status` succeeds.
- The target repository is `dayvidpham/TraceBench`, and the request maps to behavior that repository owns.
- The user described a problem, requested behavior, or outcome.

Stop and ask if ownership, the security disclosure path, permissions, or scope is unclear.

## Resolve the repository

Use explicit live repository names for every `gh` command:

```sh
GH_REPO="dayvidpham/TraceBench"
```

If the request genuinely belongs to another repository, stop and ask the user. This skill files one issue in TraceBench.

## Workflow

1. **Inspect**
   - Read `AGENTS.md`, `README.md`, and the code and nearest tests the request touches.
   - Search the repository for duplicate open issues and PRs.
   - If an issue or PR already covers the request, report it and stop unless the user asks to refine or handle that existing item.

2. **Draft**
   - State current behavior with concrete source locations when known.
   - State user impact and the smallest owned scope.
   - Define observable acceptance criteria, production-path tests, fixture expectations, and interface evidence.
   - Use `[P1]`, `[P2]`, or `[P3]` in the title only when repository practice or the user supplies priority.
   - Use normal Markdown sections and explicit GitHub references.

Use this body shape:

```md
## Problem
...

## Scope
- ...

## Acceptance
- ...

## Diagram
```text
optional ASCII flow for multi-component behavior
```

## Blocked by / Blocks
- Blocked by: none | #N
- Blocks: none | #N

## Related
- #N
```

For an epic, add a children table with priority, issue, and blocked-by columns, plus a definition of done. Use the `epic-composer` skill when the request is a set of follow-up epics or issues.

3. **Check boundaries**
   - Public API or file-format changes state the compatibility impact and the migration path.
   - Storage or migration changes name the migration, the integration tests, and any invariant documentation.
   - Generated artifacts name the generator and the freshness check that guards them.
   - Combinatorial cases belong in named YAML fixture rows with required-name manifests, not count guards.

4. **Refine and approve**
   - Present the exact repository, proposed title, and complete issue body.
   - Ask for corrections or explicit approval.
   - Present a revised draft after any material change.
   - Do not create any issue until the user explicitly approves the current draft.

5. **Create and assign**

```sh
GH_REPO="dayvidpham/TraceBench"
ASSIGNEE=$(gh api user --jq .login)
ISSUE_URL=$(gh issue create -R "$GH_REPO" --title "<approved title>" \
  --body-file <approved-body-file> --assignee "$ASSIGNEE")
ISSUE_NUMBER=${ISSUE_URL##*/}
gh issue view "$ISSUE_NUMBER" -R "$GH_REPO" --json number,url,title,assignees
```

If an issue number was not known during drafting, update the `Blocked by / Blocks` links. Do not add or change scope during this link update.

6. **Dispatch immediately**
   - Load and follow `issue-handler` with both `GH_REPO` and `ISSUE_NUMBER`.
   - The handler owns worktree creation, implementation, repository gates, PR review agents, CI, merge, main sync, and cleanup.
   - Do not implement in a main checkout before dispatch.

## Hard rules

1. Never create an issue without approval of its exact repository, title, and current body.
2. Never create a duplicate issue or PR.
3. Always pass `-R dayvidpham/TraceBench`; never rely on the current directory.
4. Always assign the created issue to the authenticated GitHub login; never guess a username.
5. Preserve approved scope. New ambiguity requires refinement and approval.
6. Never place sensitive data, tokens, credentials, personal paths, or private history in an issue.
7. After successful creation and assignment, invoke `issue-handler` immediately.
