# Peasant #344 feasibility task (publication validation refactor)

Source: `peasant-labs/peasant#344` — `refactor(push): make publication validation explicit`.
Base: `d3bc07f6568291ee70afb146409aae98d4f8e3d7` (merge-base, post-#337).
Head: `a3f5cf89297e1c33214aee96e534585641129919` (+99/-22, 6 files).

The Peasant codebase lives at `/peasant` **inside the container image** at the
base commit (base tree only — no other branches, tags, or remotes exist in
there). Do not expect network access.

## Your task

Make publication eligibility validation explicit at each consumer instead of
hiding it inside the input loader:

1. Split `LoadReadyPublicationInput` into load-only `LoadPublicationInput` plus
   a plain shared `ValidatePublicationInput` function
   (`internal/push/publication_input.go`).
2. Call validation explicitly in the three production callers:
   `internal/push/pipeline.go`, `cmd/peasant/cmd_push.go`,
   `internal/api/sync_handler.go`.
3. Remove the pipeline's redundant empty-model check; keep database integrity
   checks inside the existing coherent read transaction.
4. Preserve current errors, statuses, and redaction order (behavior-preserving
   refactor — no wire-contract, migration, or visual change).

Reference (PR body summary): same eligibility checks run before input is used
by all three callers; tests preserve refusal and no-content-on-refusal outcomes.

## Constraints

* Work only in `/peasant`. No network (`GOPROXY=off`, `go test` offline only).
* Do not change the pinned base commit or fetch new code.
* The verifier runs **outside your container** in its own locked-down phase and
  reports only pass (`1`) / fail (`0`) via `reward.txt`.

## Check locally (inside the sandbox)

```bash
go test -count=1 ./internal/api/ -run 'TestHandleSyncRedactionsRefusesUnpublishableCapture|TestHandleSyncPush_TheShareDoorGivesThePipelineARedactor' -v
```

Pass = both tests `PASS`. At the base commit the test file
`internal/api/sync_publication_test.go` does not exist, so this fails —
your fix must make it pass.
