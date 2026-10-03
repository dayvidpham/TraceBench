---
name: review-wave
description: Run a coordinated three-reviewer review of one or two sibling pull requests in the TraceBench repository, curate the findings, and publish one human+agent report. Use when asked to run a review wave, review a PR, or produce a curated review report.
---

# Review wave

Requirements for reviewing one or two sibling pull requests and publishing a curated report.
Reviewer side: the `/reviewer` skill and the `reviewer` subagent. Operating mechanics:
`references/operations.md`.

## Wave composition

- Three reviewers per wave; each reviewer looks over every PR in the wave.
- Protocol: review wave → curate findings → structured report.

## Review content, per PR

- Problem statement, constraints, requirements, acceptance criteria, developed solution, tradeoffs.
- A suggested fix for every finding, with alternatives listed and their tradeoffs.
- Explanatory ASCII diagrams; C4 diagrams via the `c4-model` skill.
- A recommended fix gets a diagram when it is not obvious from the description; blockers always do.

## Curation

- Curate first: keep only findings that are actually useful and impactful.
- Reviewer findings are not gospel: verify load-bearing claims before keeping them.
- A one-line documentation change can be very impactful when the prior state is a large error that
  could mislead many agents, or when the proposed state improves things much more.

## Report format (the posted comment)

- One comment containing two documents: one for humans, one for agents. Human part first.
- Human part: short bullet points; problem statement, findings, proposed fixes, tradeoffs; one
  table; a few ASCII diagrams; glossary at the end.
- Agent part: artifact and provenance, acceptance status per criterion, one section per finding
  (location, symptom, evidence, impact, required change, tests, alternatives), the verification
  run, and the C4 diagram.
- Terminology: only industry-standard terms or terms used in the codebase; no invented terms.
- Show the absolute path to the report, then post it.

## Lifecycle

- Merged pull requests need no follow-up.
- Post after user approval, unless the user has already said to post.
- Report comments carry the reviewed SHA; every re-review walks the entire prior checklist against
  the current SHA.

## Source prompts (cleaned: PR links and asides removed)

> Let's run a review wave on these PRs.
>
> Run a review wave with three @reviewer that will look over both PRs. I want to see the problem statement, constraints, requirements, acceptance criteria, developed solution, and tradeoffs. Include explanatory ASCII diagrams and also @c4-model .

> We should put all of this output into a document. Let's harmonize all the reviews, produce a review report for each PR, and include suggested fixes for each issue, with alternatives listed and their tradeoffs. these should come with explanatory ASCII diagrams that demonstrate how the recommended fix will solve each finding. However, we should first curate the findings for only actually useful and impactful findings: don't accept the reviewer findings as gospel. This in mind, even one line documentation changes can be very impactful, if the prior state was a large error which could mislead many agents, or if the proposed state can improve things much more.

> the report comment should actually be two documents. one for humans, and one for agents. one table, a few ascii diagrams is nice for the human part. human part goes first. glossary is nice at end of the human part. problem statement, findings, proposed fixes, tradeoffs. but short and bullet point. also use only industry-standard terms or terms in the codebase, not your own invented terms. once this is reconfigured, show me the aboslute path to it and then post it.

## Files

- `templates/wave-brief.md` — the brief the reviewers receive.
- `templates/review-comment.md` — the two-part report skeleton.
- `references/operations.md` — operating mechanics: isolation, reviewer prompts, curation,
  posting, housekeeping.
