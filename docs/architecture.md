# TraceBench architecture

This page shows how the parts of TraceBench connect. It goes from the high level to the low level:

1. [System context](#system-context): TraceBench, its user, and the systems it talks to.
2. [Containers](#containers): the runtime units and data stores inside TraceBench.
3. [Components](#components): the Go packages, the Python loader modules, and the snapshot module.
4. [Deployment](#deployment): where each container runs.
5. [Dynamic views](#dynamic-views): the main flows, in numbered order.
6. [Call sequences](#call-sequences): the same flows at the level of functions and files.
7. [Package map](#package-map): the role of each Go package, Python module, and task directory.

All diagrams are Mermaid, so GitHub renders them in place. The structural views follow the C4
model vocabulary of [`.claude/skills/c4-model`](../.claude/skills/c4-model/SKILL.md) and
are drawn as Mermaid flowcharts: each box names its element, its C4 type in square brackets,
and a short description, and a dashed outline marks a boundary. Every arrow is one relationship,
read as "source, label (technology), target". The dynamic views and the call sequences are
Mermaid sequence diagrams. Colors follow the C4 convention: dark blue for people, blue for
TraceBench's own elements, and grey for external systems.

The libraries `github.com/peasant-labs/schema` (wire contract), `github.com/peasant-labs/redact`
(redaction engine), `gopkg.in/yaml.v3`, `modernc.org/sqlite`, and `huggingface_hub` are not
containers. They show as technology text or in the package map, never as boxes.

Provenance: derived from the source tree on this branch. The main sources are
`cmd/tracebench-sample`, `internal/`, `loaders/tracebench_corpus`, `snapshot/`, `tasks/`, and
`scripts/`. When the code and a diagram disagree, the diagram is wrong. Re-derive the tables from
the source before you redraw.

## System context

TraceBench is local-first. It reads merged pull requests and the agent sessions recorded on the
developer's machine, samples a train/val/test corpus with the raw transcripts, and turns corpus
entries into Harbor benchmark tasks. The corpus is published to HuggingFace; task runs happen in
Harbor sandboxes whose agent and verifier phases have no network. Nothing else leaves the machine:
there is no telemetry and no background upload.

Elements:

| Name | Type | Description |
|---|---|---|
| benchmark engineer | Person | Samples the corpus, builds tasks, and runs benchmarks. |
| TraceBench | Software System | Samples agent traces from merged pull requests and generates Harbor benchmark tasks. |
| peasant analytics database | Software System, external | The local `peasant.db` with recorded agent sessions, commits, and pulled transcripts. |
| GitHub | Software System, external | Hosts the merged pull requests of the target repositories. |
| target git repository | Software System, external | A local clone of the repository whose pull requests become tasks. |
| HuggingFace Hub | Software System, external | Hosts the published TraceBench dataset. |
| Harbor | Software System, external | Runs benchmark tasks in network-restricted sandboxes. |

Relationships:

| Source | Target | Intent | Technology | Trigger |
|---|---|---|---|---|
| benchmark engineer | TraceBench | runs the sampler, the loader, and the snapshot CLI | terminal | user |
| benchmark engineer | Harbor | runs generated tasks | harbor CLI | user |
| benchmark engineer | HuggingFace Hub | publishes the dump | HuggingFace tooling | user |
| TraceBench | peasant analytics database | reads sessions, commits, and artifacts | SQLite, read-only | index, sample, dump |
| TraceBench | GitHub | lists merged pull requests, resolves commits | gh CLI, HTTPS | index |
| TraceBench | target git repository | resolves the develop boundary, materializes trees, extracts tests, diffs the oracle | git CLI | task, oracle, pipeline, snapshot |
| TraceBench | HuggingFace Hub | downloads the published dump | HTTPS | fetch, load |

```mermaid
flowchart TB
  dev["<b>benchmark engineer</b><br/>[Person]<br/>Samples the corpus, builds tasks,<br/>and runs benchmarks."]:::person
  tb["<b>TraceBench</b><br/>[Software System]<br/>Samples agent traces from merged<br/>pull requests and generates<br/>Harbor benchmark tasks."]:::system
  db["<b>peasant analytics database</b><br/>[Software System, external]<br/>Local peasant.db with recorded<br/>agent sessions and commits."]:::external
  gh["<b>GitHub</b><br/>[Software System, external]<br/>Hosts merged pull requests of<br/>the target repositories."]:::external
  git["<b>target git repository</b><br/>[Software System, external]<br/>A local clone: commits, trees,<br/>and the develop boundary."]:::external
  hf["<b>HuggingFace Hub</b><br/>[Software System, external]<br/>Hosts the published<br/>TraceBench dataset."]:::external
  harbor["<b>Harbor</b><br/>[Software System, external]<br/>Runs benchmark tasks in<br/>network-restricted sandboxes."]:::external

  dev -->|"runs the sampler, the loader,<br/>and the snapshot CLI (terminal)"| tb
  dev -->|"runs generated tasks<br/>(harbor CLI)"| harbor
  dev -->|"publishes the dump<br/>(HuggingFace tooling)"| hf
  tb -->|"reads sessions, commits,<br/>and artifacts (SQLite, read-only)"| db
  tb -->|"lists merged pull requests,<br/>resolves commits (gh CLI, HTTPS)"| gh
  tb -->|"resolves the boundary, materializes<br/>trees, diffs the oracle (git CLI)"| git
  tb -->|"downloads the published dump<br/>(HTTPS)"| hf

  classDef person fill:#08427b,stroke:#052e56,color:#fff
  classDef system fill:#1168bd,stroke:#0b4884,color:#fff
  classDef container fill:#438dd5,stroke:#2e6295,color:#fff
  classDef component fill:#85bbf0,stroke:#5d82a8,color:#000
  classDef external fill:#999999,stroke:#6b6b6b,color:#fff
```

## Containers

TraceBench is a set of command-line tools plus files, not a service. `tracebench-sample` (Go)
builds the corpus; `tracebench-corpus` (Python) turns corpus entries into task payloads and
runnable Harbor tasks, and its `pipeline` command writes a Harbor job config per run;
`snapshot` (Go, its own module) materializes repository trees and git history at a cutoff; it is
a standalone tool. The loader writes each task's secure worktree itself (`git pack-objects`
over the ancestry of `tree_commit`). Everything runs on the developer's
workstation.

Elements:

| Name | Type | Technology | Description |
|---|---|---|---|
| tracebench-sample | Container | Go | Corpus CLI: index, sample, run, dump, fetch. |
| tracebench-corpus | Container | Python, uv | Loader CLI: list, bundle, bundle-all, task, skeleton, test-manifest, oracle, pipeline. |
| snapshot | Container | Go | Snapshot CLI: repo tree, trace, and history at a cutoff. |
| corpus tree | Container | JSON, JSONL | `corpus/index`, `corpus/dataset`, and `corpus/dump` artifacts. |
| task payload | Container | JSON, JSONL | Per-pull-request payload for one task. |
| snapshot output | Container | JSON, git tree | `history.json`, the materialized `repo/` tree, and `traces/`. |
| Harbor task | Container | TOML, shell, Python | Task directory: `task.toml`, `instruction.md`, `environment/`, `solution/`, `tests/` (golden suite and verifier). |
| Harbor job config | Container | YAML or JSON | One per run id: `job_name`, `n_attempts`, tasks, agents, and `TRACEBENCH_RUN_ID`. |

Relationships:

| Source | Target | Intent | Technology |
|---|---|---|---|
| benchmark engineer | tracebench-sample | runs corpus commands | terminal |
| benchmark engineer | tracebench-corpus | runs loader commands | terminal |
| benchmark engineer | snapshot | runs snapshot commands | terminal |
| benchmark engineer | Harbor task | edits the instruction | editor |
| benchmark engineer | Harbor | runs tasks and jobs | harbor CLI |
| tracebench-sample | peasant analytics database | reads sessions, commits, and artifacts; exports captures | SQLite read-only, peasant CLI |
| tracebench-sample | GitHub | lists merged pull requests, resolves commits | gh CLI, HTTPS |
| tracebench-sample | corpus tree | writes index, dataset, and dump artifacts | files |
| tracebench-sample | HuggingFace Hub | fetches the published dump | HTTPS |
| tracebench-corpus | corpus tree | reads the dump, writes per-PR bundles | files |
| tracebench-corpus | HuggingFace Hub | downloads the dump when no local copy exists | huggingface_hub |
| tracebench-corpus | task payload | writes and rebuilds payloads | files |
| tracebench-corpus | target git repository | resolves the boundary, extracts the golden suite, diffs and verifies the oracle | git CLI |
| tracebench-corpus | Harbor task | writes runnable tasks | files |
| tracebench-corpus | Harbor job config | writes the job config for a run | files |
| snapshot | target git repository | reads commits, trees, and archives | git CLI |
| snapshot | peasant analytics database | resolves a PR start through the peasant CLI | exec |
| snapshot | snapshot output | writes history.json, repo/, and traces/ | files |
| Harbor | Harbor task | reads task.toml, environment/, solution/, and tests/ | harbor CLI |
| Harbor | Harbor job config | reads the run's tasks and agents | harbor CLI |

```mermaid
flowchart TB
  dev["<b>benchmark engineer</b><br/>[Person]<br/>Samples the corpus, builds tasks,<br/>and runs benchmarks."]:::person

  subgraph tb["TraceBench [Software System]"]
    sample["<b>tracebench-sample</b><br/>[Container: Go]<br/>Corpus CLI: index, sample,<br/>run, dump, fetch."]:::container
    loader["<b>tracebench-corpus</b><br/>[Container: Python, uv]<br/>Loader CLI: task, skeleton,<br/>oracle, pipeline, and more."]:::container
    snap["<b>snapshot</b><br/>[Container: Go]<br/>Repo tree, trace, and history<br/>at a cutoff."]:::container
    corpustree[("<b>corpus tree</b><br/>[Container: JSON, JSONL]<br/>corpus/index, corpus/dataset,<br/>corpus/dump.")]:::container
    payload[("<b>task payload</b><br/>[Container: JSON, JSONL]<br/>pr.json, prior-traces/, repo/,<br/>tests/, solution/, repo-request.json.")]:::container
    snapout[("<b>snapshot output</b><br/>[Container: JSON, git tree]<br/>history.json, repo/, traces/.")]:::container
    taskdir[("<b>Harbor task</b><br/>[Container: TOML, shell, Python]<br/>task.toml, instruction.md,<br/>environment/, solution/, tests/.")]:::container
    jobcfg[("<b>Harbor job config</b><br/>[Container: YAML or JSON]<br/>job_name, n_attempts,<br/>TRACEBENCH_RUN_ID.")]:::container
  end

  db["<b>peasant analytics database</b><br/>[Software System, external]"]:::external
  gh["<b>GitHub</b><br/>[Software System, external]"]:::external
  git["<b>target git repository</b><br/>[Software System, external]"]:::external
  hf["<b>HuggingFace Hub</b><br/>[Software System, external]"]:::external
  harbor["<b>Harbor</b><br/>[Software System, external]"]:::external

  dev -->|"runs corpus commands<br/>(terminal)"| sample
  dev -->|"runs loader commands<br/>(terminal)"| loader
  dev -->|"runs snapshot commands<br/>(terminal)"| snap
  dev -->|"edits the instruction<br/>(editor)"| taskdir
  dev -->|"runs tasks and jobs<br/>(harbor CLI)"| harbor

  sample -->|"reads sessions, commits, artifacts;<br/>exports captures (SQLite, peasant CLI)"| db
  sample -->|"lists pull requests, resolves commits<br/>(gh CLI, HTTPS)"| gh
  sample -->|"writes index, dataset, dump<br/>(files)"| corpustree
  sample -->|"fetches the dump<br/>(HTTPS)"| hf
  loader -->|"reads the dump, writes bundles<br/>(files)"| corpustree
  loader -->|"downloads the dump<br/>(huggingface_hub)"| hf
  loader -->|"writes payloads<br/>(files)"| payload
  loader -->|"writes runnable tasks<br/>(files)"| taskdir
  loader -->|"writes the job config<br/>(files)"| jobcfg
  loader -->|"extracts tests, diffs the oracle<br/>(git CLI)"| git
  loader -->|"packs the truncated history<br/>(git CLI, read-only)"| git
  snap -->|"reads commits and trees<br/>(git CLI)"| git
  snap -->|"resolves a PR start<br/>(exec)"| db
  snap -->|"writes history and trees<br/>(files)"| snapout
  harbor -->|"reads the task<br/>(harbor CLI)"| taskdir
  harbor -->|"reads the job config<br/>(harbor CLI)"| jobcfg

  style tb fill:none,stroke:#444,stroke-dasharray:6 4

  classDef person fill:#08427b,stroke:#052e56,color:#fff
  classDef system fill:#1168bd,stroke:#0b4884,color:#fff
  classDef container fill:#438dd5,stroke:#2e6295,color:#fff
  classDef component fill:#85bbf0,stroke:#5d82a8,color:#000
  classDef external fill:#999999,stroke:#6b6b6b,color:#fff
```

### Corpus tree

| Path | Contents |
|---|---|
| `corpus/index/` | `merged_prs.json`, `sessions.json`, `traces.json`, `commits.json`, `session_relations.json`, `summary.json`. |
| `corpus/dataset/` | `manifest.json` and `<split>/<owner--name>/pr-<number>/` with `pr.json` and `transcripts/`. |
| `corpus/dump/` | `manifest.json`, `metadata.jsonl`, `transcripts/<session_id>.jsonl`, `pull_requests.jsonl`, `traces.jsonl`. |
| `corpus/dump-village-pull/` | The same dump built from pulled transcripts, plus `village_pulls.jsonl`. |

### Task payload

| Path | Contents |
|---|---|
| `pr.json` | The pull request, enriched from `--index` (`merge_commit`, `head_oid`, `base_ref`). |
| `prior-traces/` | `traces.jsonl`, `metadata.jsonl`, `transcripts/`, and `manifest.json` for the prior context. |
| `repo/` | The full real history truncated at `tree_commit` (the pre-PR state): every ancestor with real SHAs, no remotes, nothing at or after the PR. Empty when built by `task` alone. |
| `tests/` | The golden suite: every file at `merge_commit` matching the test patterns, plus `tests/manifest.json` (commit, patterns, paths). Written by `task --materialize-tests` and by `pipeline`. |
| `test-manifest.json` | The case catalog: every top-level Go test case at `merge_commit`; PR-changed cases flagged `golden`. |
| `solution/` | `oracle.patch` (the merge diff) and `solve.sh` (apply, then build), written by `oracle` and by `pipeline`. |
| `repo-request.json` | The contract for the repository tooling: `tree_commit`, `trace_cutoff`, test patterns, glob dialect, merge-commit policy. |
| `task.json` | Payload summary: cutoff, target configuration, counts, exclusions, and the `oracle` block. |

### Harbor task

| Path | Contents |
|---|---|
| `task.toml` | Identity, metadata, timeouts, network policy, `[environment].docker_image` and `workdir`. |
| `instruction.md` | Agent instructions, scaffolded from the pull request. |
| `environment/` | `repo/` and `prior-traces/`, uploaded into the container workdir at environment start. |
| `tests/` | `golden/` (the merged-state suite), `manifest.json`, `test-manifest.json`, `verifier-config.json`, `verifier.py`, `repo-request.json`, and `test.sh`. Verifier only. |
| `solution/` | `oracle.patch` and `solve.sh` when the payload has an oracle; otherwise a `solve.sh` placeholder that exits non-zero. |

### Harbor job config

`pipeline` writes `<dest>/job-config.yaml` (or `job-config.json` without PyYAML) for each run.

| Key | Contents |
|---|---|
| `job_name` | The run id: `--run-id`, or a generated UUIDv7 prefixed by `--run-label`. |
| `n_attempts` | `3`. Repeats are separate runs; group them by cell. |
| `tasks` | One `{path, source}` per task that was built; `source` is the run id. |
| `agents` | One agent: the configuration's harness, model, and `reasoning_effort`, or `oracle`; `env.TRACEBENCH_RUN_ID`. |
| `verifier.env` | `TRACEBENCH_RUN_ID`. |

## Components

### Corpus build path

`tracebench-sample` builds the index, samples the corpus, and writes the dump. The index links
each recorded session to merged pull requests through head-ref keys, same-issue open windows, and
commit associations; a session may link to several pull requests. The collector closes over
parent, child, and fork lineage so a bundle carries the session forest around its attributed
work. The dump writes `schema.UnifiedMetadata` records and `schema.TranscriptContent` envelopes,
redacted through the redact engine, and exports sessions whose raw source is not a plain file
through the peasant CLI.

| Component | Package | Description |
|---|---|---|
| cli | `cmd/tracebench-sample` | Command tree: index, sample, run, dump, fetch. |
| prindex | `internal/prindex` | Merged PRs from gh, session keys, attribution, commit mapping, lineage relations, index persistence. |
| peasantstore | `internal/peasantstore` | Read-only SQLite reads: sessions, commits, lineage edges, artifacts, pulled transcripts, transcript exports. |
| sampler | `internal/sampler` | Time- and size-stratified selection; group-aware split assignment. |
| collector | `internal/collector` | Materializes bundles: transcript copies or exports, per-PR records, dataset manifest. |
| dump writer | `internal/dump` | Writes metadata, transcript envelopes, indexes, and the dump manifest; redacts through the redact engine. |
| fetch | `internal/fetch` | Downloads the published dump and verifies each transcript's content hash. |

Relationships:

| Source | Target | Intent | Technology |
|---|---|---|---|
| benchmark engineer | cli | runs corpus commands | terminal |
| cli | prindex | builds the merged-PR index | Go call |
| cli | sampler | selects splits | Go call |
| cli | collector | materializes bundles | Go call |
| cli | dump writer | writes the dump | Go call |
| cli | fetch | downloads the published dump | Go call |
| prindex | GitHub | lists merged PRs, resolves commits | gh CLI, HTTPS |
| prindex | peasantstore | reads sessions and commits | Go call |
| peasantstore | peasant analytics database | reads rows, exports captures | SQL read-only, peasant CLI |
| collector | peasantstore | opens transcripts | Go call |
| dump writer | peasantstore | reads artifacts, exports captures | Go call, peasant CLI |
| dump writer | corpus tree | writes metadata, transcripts, indexes | files |
| fetch | HuggingFace Hub | downloads dump files | HTTPS |
| fetch | corpus tree | writes the fetched dump | files |

```mermaid
flowchart TB
  dev["<b>benchmark engineer</b><br/>[Person]"]:::person

  subgraph samplecli["tracebench-sample [Container: Go]"]
    cli["<b>cli</b><br/>[Component: Go]<br/>cmd/tracebench-sample: index,<br/>sample, run, dump, fetch."]:::component
    pidx["<b>prindex</b><br/>[Component: Go package]<br/>Merged PRs, session keys,<br/>attribution, commit mapping."]:::component
    pstore["<b>peasantstore</b><br/>[Component: Go package]<br/>Read-only SQLite access and<br/>transcript exports."]:::component
    sel["<b>sampler</b><br/>[Component: Go package]<br/>Stratified selection and<br/>split assignment."]:::component
    col["<b>collector</b><br/>[Component: Go package]<br/>Transcript collection and<br/>dataset manifest."]:::component
    writer["<b>dump writer</b><br/>[Component: Go package]<br/>internal/dump: schema records,<br/>redaction, dump manifest."]:::component
    fetchc["<b>fetch</b><br/>[Component: Go package]<br/>HuggingFace download and<br/>hash verification."]:::component
  end

  db["<b>peasant analytics database</b><br/>[Software System, external]"]:::external
  gh["<b>GitHub</b><br/>[Software System, external]"]:::external
  hf["<b>HuggingFace Hub</b><br/>[Software System, external]"]:::external
  corpustree[("<b>corpus tree</b><br/>[Container: JSON, JSONL]")]:::container

  dev -->|"runs corpus commands<br/>(terminal)"| cli
  cli -->|"builds the index<br/>(Go call)"| pidx
  cli -->|"selects splits<br/>(Go call)"| sel
  cli -->|"materializes bundles<br/>(Go call)"| col
  cli -->|"writes the dump<br/>(Go call)"| writer
  cli -->|"downloads the dump<br/>(Go call)"| fetchc
  pidx -->|"lists PRs, resolves commits<br/>(gh CLI, HTTPS)"| gh
  pidx -->|"reads sessions and commits<br/>(Go call)"| pstore
  pstore -->|"reads rows, exports captures<br/>(SQL read-only, peasant CLI)"| db
  col -->|"opens transcripts<br/>(Go call)"| pstore
  writer -->|"reads artifacts<br/>(Go call)"| pstore
  writer -->|"writes metadata, transcripts,<br/>indexes (files)"| corpustree
  fetchc -->|"downloads dump files<br/>(HTTPS)"| hf
  fetchc -->|"writes the fetched dump<br/>(files)"| corpustree

  style samplecli fill:none,stroke:#444,stroke-dasharray:4 4

  classDef person fill:#08427b,stroke:#052e56,color:#fff
  classDef system fill:#1168bd,stroke:#0b4884,color:#fff
  classDef container fill:#438dd5,stroke:#2e6295,color:#fff
  classDef component fill:#85bbf0,stroke:#5d82a8,color:#000
  classDef external fill:#999999,stroke:#6b6b6b,color:#fff
```

### Loader path

`tracebench-corpus` loads the dump and produces, per pull request, a task payload. The prior
context is every trace of the same codebase family merged before the pull request's develop
boundary; the pull request's own sessions are excluded, and prior sessions that ran past the
boundary are cut. `develop` keeps one commit per pull request (squash-rebase), so the boundary is
the parent of the pull request's squash commit; `--repo-dir` resolves it by commit ancestry, and
`merged_at` is the portable proxy otherwise. `skeleton` turns a payload into a Harbor task
directory that references a shared base image and uploads task data at environment start.

`pipeline` chains the steps per pull request: payload with the golden suite and the case
catalog, the secure worktree, the oracle, and the skeleton with the verifier. It then writes the
Harbor job config. A failure fails that task only and names the failed part. The repository
adaptation spec supplies the test command (for the verifier) and the build command (for the
oracle). The verifier is copied into each task and runs inside the sandbox.

| Component | Module | Description |
|---|---|---|
| cli | `tracebench_corpus/cli.py` | Commands: list, bundle, bundle-all, task, skeleton, test-manifest, oracle, pipeline. |
| pipeline driver | `tracebench_corpus/pipeline.py` | Per-PR build loop, per-task failure naming, run id, Harbor job config. |
| corpus loader | `tracebench_corpus/corpus.py` | Loads a local or HuggingFace dump; per-PR bundles; transcript turns. |
| task builder | `tracebench_corpus/task.py` | Boundary and cutoff resolution, prior-trace selection, session cuts, repo-request, destination hygiene. |
| golden suite | `tracebench_corpus/golden.py` | Test files at `merge_commit` matching the test patterns; `tests/manifest.json`; the canonical doublestar matcher. |
| case catalog | `tracebench_corpus/test_manifest.py` | Top-level Go test cases at `merge_commit`; PR-changed cases flagged `golden`. |
| secure worktree | `tracebench_corpus/worktree.py` | Packs the ancestors of `tree_commit` into a fresh repo; asserts HEAD, tree, source-equal commit count, not shallow, no remotes, fix absent, clean `fsck`. |
| oracle | `tracebench_corpus/oracle.py` | Merge diff, `solve.sh`, equivalence check against `merge_commit^{tree}`, the `task.json` oracle block. |
| repository adaptation spec | `tracebench_corpus/repository_spec.py` | Test and build command per repository; first match wins; Go/Peasant default. |
| skeleton builder | `tracebench_corpus/skeleton.py` | task.toml, instruction.md, environment upload, `test.sh`, verifier config and verifier copy. |
| verifier | `tracebench_corpus/verifier.py` | In-sandbox grading: removes pre-PR tests, overlays the golden suite, runs the test command, writes `reward.txt` and `test-results.json`. |
| target configuration | `tracebench_corpus/target_config.py` | Spec validation and the harness/model/thinking record. |

Relationships:

| Source | Target | Intent | Technology |
|---|---|---|---|
| benchmark engineer | cli | runs loader commands | terminal |
| cli | corpus loader | loads the dump, materializes bundles | Python call |
| corpus loader | corpus tree | reads dump files | files |
| corpus loader | HuggingFace Hub | downloads the dump | huggingface_hub |
| cli | task builder | builds a payload | Python call |
| task builder | corpus loader | reads traces, metadata, transcripts | Python call |
| task builder | target git repository | resolves the boundary and ancestry | git CLI |
| task builder | target configuration | validates and records the target | Python call |
| task builder | task payload | writes and rebuilds the payload | files |
| cli | skeleton builder | generates the task directory | Python call |
| skeleton builder | task payload | reads the payload and repo-request | files |
| skeleton builder | Harbor task | writes task.toml, environment, tests | files |
| skeleton builder | verifier | copies verifier.py into tests/ | files |
| cli | pipeline driver | builds tasks from a PR list | Python call |
| pipeline driver | task builder | builds the payload with the golden suite | Python call |
| task builder | golden suite | extracts merged-state test files | Python call |
| task builder | case catalog | writes test-manifest.json | Python call |
| golden suite | target git repository | runs ls-tree and show at merge_commit | git CLI |
| pipeline driver | secure worktree | materializes repo/ | Python call |
| pipeline driver | repository adaptation spec | selects the test and build command | Python call |
| pipeline driver | oracle | builds and writes the oracle | Python call |
| oracle | target git repository | diffs, applies in a temporary worktree, compares trees | git CLI |
| pipeline driver | skeleton builder | writes the task | Python call |
| pipeline driver | Harbor job config | writes the job config | files |

```mermaid
flowchart TB
  dev["<b>benchmark engineer</b><br/>[Person]"]:::person

  subgraph loadersys["tracebench-corpus [Container: Python, uv]"]
    lcli["<b>cli</b><br/>[Component: Python]<br/>list, bundle, bundle-all,<br/>task, skeleton."]:::component
    cor["<b>corpus loader</b><br/>[Component: Python]<br/>Loads the dump, bundles,<br/>and transcript turns."]:::component
    tb["<b>task builder</b><br/>[Component: Python]<br/>Boundary, prior traces,<br/>session cuts, repo-request."]:::component
    tcfg["<b>target configuration</b><br/>[Component: Python]<br/>Spec validation and the<br/>harness/model/thinking record."]:::component
    skel["<b>skeleton builder</b><br/>[Component: Python]<br/>Harbor task directory and<br/>verifier script."]:::component
    pipe["<b>pipeline driver</b><br/>[Component: Python]<br/>Per-PR build loop and<br/>Harbor job config."]:::component
    gold["<b>golden suite</b><br/>[Component: Python]<br/>Merged-state test files and<br/>the doublestar matcher."]:::component
    cat["<b>case catalog</b><br/>[Component: Python]<br/>Go test cases at merge_commit,<br/>golden flags."]:::component
    wt["<b>secure worktree</b><br/>[Component: Python]<br/>Truncated full history,<br/>tree and history checks."]:::component
    orc["<b>oracle</b><br/>[Component: Python]<br/>Merge diff, solve.sh,<br/>equivalence check."]:::component
    spec["<b>repository adaptation spec</b><br/>[Component: Python]<br/>Test and build command<br/>per repository."]:::component
    ver["<b>verifier</b><br/>[Component: Python, stdlib]<br/>Runs in the sandbox;<br/>reward.txt, test-results.json."]:::component
  end

  hf["<b>HuggingFace Hub</b><br/>[Software System, external]"]:::external
  git["<b>target git repository</b><br/>[Software System, external]"]:::external
  corpustree[("<b>corpus tree</b><br/>[Container: JSON, JSONL]")]:::container
  payload[("<b>task payload</b><br/>[Container: JSON, JSONL]")]:::container
  taskdir[("<b>Harbor task</b><br/>[Container: TOML, shell, Python]")]:::container
  jobcfg[("<b>Harbor job config</b><br/>[Container: YAML or JSON]")]:::container

  dev -->|"runs loader commands<br/>(terminal)"| lcli
  lcli -->|"loads the dump<br/>(Python call)"| cor
  cor -->|"reads dump files<br/>(files)"| corpustree
  cor -->|"downloads the dump<br/>(huggingface_hub)"| hf
  lcli -->|"builds a payload<br/>(Python call)"| tb
  tb -->|"reads traces and transcripts<br/>(Python call)"| cor
  tb -->|"resolves the boundary<br/>(git CLI)"| git
  tb -->|"records the target<br/>(Python call)"| tcfg
  tb -->|"writes the payload<br/>(files)"| payload
  lcli -->|"generates the task<br/>(Python call)"| skel
  skel -->|"reads the payload<br/>(files)"| payload
  skel -->|"writes the task<br/>(files)"| taskdir
  skel -->|"copies verifier.py<br/>(files)"| ver
  lcli -->|"builds tasks from a PR list<br/>(Python call)"| pipe
  pipe -->|"builds the payload<br/>(Python call)"| tb
  tb -->|"extracts the golden suite<br/>(Python call)"| gold
  tb -->|"writes the case catalog<br/>(Python call)"| cat
  gold -->|"reads blobs at merge_commit<br/>(git CLI)"| git
  pipe -->|"selects commands<br/>(Python call)"| spec
  pipe -->|"materializes repo/<br/>(Python call)"| wt
  pipe -->|"builds the oracle<br/>(Python call)"| orc
  orc -->|"diffs and verifies<br/>(git CLI)"| git
  pipe -->|"writes the task<br/>(Python call)"| skel
  pipe -->|"writes the job config<br/>(files)"| jobcfg

  style loadersys fill:none,stroke:#444,stroke-dasharray:4 4

  classDef person fill:#08427b,stroke:#052e56,color:#fff
  classDef system fill:#1168bd,stroke:#0b4884,color:#fff
  classDef container fill:#438dd5,stroke:#2e6295,color:#fff
  classDef component fill:#85bbf0,stroke:#5d82a8,color:#000
  classDef external fill:#999999,stroke:#6b6b6b,color:#fff
```

### Snapshot path

`snapshot` is a separate Go module. It resolves a cutoff (an absolute date, inclusive, a pull
request start, exclusive, or one exact commit), truncates the git history and the tree at the
selected commit, collects trace files, and writes a deterministic snapshot: `history.json` with
`repo_sha`, `tree_sha`, and a `manifest_sha256`, and with `--materialize`, the exact
`git archive` tree (no `.git`) and trace copies.

The commit cutoff (`--cutoff-type commit --commit <sha>`) pins the tree of that commit,
independent of HEAD; it works when the commit is unreachable from HEAD and in a bare clone. Its
history is the commit's ancestry. The loader's secure worktree does not use this mode: it packs
the ancestry of `tree_commit` directly (`git pack-objects --revs`), because the `pr` cutoff is a
time cut at the pull request start, not `merge_commit^`, and the payload ships a real repository
with `.git`.

| Component | File | Description |
|---|---|---|
| cli | `snapshot/cmd/snapshot` | Flags: repo, cutoff type, peasant binary, output, materialize. |
| cutoff resolver | `snapshot/cutoff.go` | Date (inclusive), PR start (exclusive), or one exact commit. |
| peasant client | `snapshot/peasant.go` | Resolves a PR number to its start time through the peasant binary contract. |
| repo snapshotter | `snapshot/repo.go` | History list (time-based, or ancestry for a commit cutoff), tree list, `git archive` materialization. |
| trace collector | `snapshot/trace.go` | Trace files by event time; stub or directory provider. |
| assembler | `snapshot/api.go` | Snapshot assembly, `repo_sha` and `tree_sha`, canonical manifest hash, history.json, materialize. |

Relationships:

| Source | Target | Intent | Technology |
|---|---|---|---|
| benchmark engineer | cli | runs snapshot commands | terminal |
| cli | assembler | builds and writes the snapshot | Go call |
| assembler | cutoff resolver | resolves the cutoff | Go call |
| assembler | repo snapshotter | lists history and trees | Go call |
| assembler | trace collector | lists trace files | Go call |
| cutoff resolver | peasant client | resolves a PR start | Go call |
| peasant client | peasant analytics database | runs the peasant CLI | exec |
| repo snapshotter | target git repository | runs log, ls-tree, archive | git CLI |
| assembler | snapshot output | writes history.json and trees | files |

```mermaid
flowchart TB
  dev["<b>benchmark engineer</b><br/>[Person]"]:::person

  subgraph snapsys["snapshot [Container: Go]"]
    scli["<b>cli</b><br/>[Component: Go]<br/>snapshot/cmd/snapshot:<br/>cutoff, output, materialize."]:::component
    cut["<b>cutoff resolver</b><br/>[Component: Go]<br/>Date inclusive, PR start<br/>exclusive, or exact commit."]:::component
    pc["<b>peasant client</b><br/>[Component: Go]<br/>Resolves a PR start through<br/>the peasant binary."]:::component
    repo["<b>repo snapshotter</b><br/>[Component: Go]<br/>History, tree, and<br/>git archive output."]:::component
    trc["<b>trace collector</b><br/>[Component: Go]<br/>Trace files by event time;<br/>stub or directory provider."]:::component
    api["<b>assembler</b><br/>[Component: Go]<br/>Snapshot, tree_sha, manifest hash,<br/>history.json, materialize."]:::component
  end

  loader["<b>tracebench-corpus</b><br/>[Container: Python, uv]"]:::container

  db["<b>peasant analytics database</b><br/>[Software System, external]"]:::external
  git["<b>target git repository</b><br/>[Software System, external]"]:::external
  snapout[("<b>snapshot output</b><br/>[Container: JSON, git tree]")]:::container

  dev -->|"runs snapshot commands<br/>(terminal)"| scli
  scli -->|"builds the snapshot<br/>(Go call)"| api
  api -->|"resolves the cutoff<br/>(Go call)"| cut
  api -->|"lists history and trees<br/>(Go call)"| repo
  api -->|"lists trace files<br/>(Go call)"| trc
  cut -->|"resolves a PR start<br/>(Go call)"| pc
  pc -->|"runs the peasant CLI<br/>(exec)"| db
  repo -->|"runs log, ls-tree, archive<br/>(git CLI)"| git
  api -->|"writes history and trees<br/>(files)"| snapout

  style snapsys fill:none,stroke:#444,stroke-dasharray:4 4

  classDef person fill:#08427b,stroke:#052e56,color:#fff
  classDef system fill:#1168bd,stroke:#0b4884,color:#fff
  classDef container fill:#438dd5,stroke:#2e6295,color:#fff
  classDef component fill:#85bbf0,stroke:#5d82a8,color:#000
  classDef external fill:#999999,stroke:#6b6b6b,color:#fff
```

## Deployment

Everything builds and runs on the developer workstation. The sampler, the loader, and the
snapshot CLI run as local commands. The peasant database lives at
`$XDG_DATA_HOME/peasant/peasant.db` and is opened read-only. Harbor builds the task image
`FROM` the runtime image `tracebench/task-runtime` (the `tracebench/peasant-base` toolchain without the peasant clone or Go build cache),
starts the sandbox, uploads `environment/` into the workdir, and runs three phases:

- **environment**: public network, for the image build and task-data upload;
- **agent**: no network, the agent works on `workdir/repo`;
- **verifier**: no network, `tests/` is copied to `/tests`, and `test.sh` runs `verifier.py`:
  it removes the pre-PR tests, overlays the golden suite on `workdir/repo`, runs the test
  command, and writes the reward (`passed / total`) to `/logs/verifier/reward.txt` and the
  per-case report to `/logs/verifier/test-results.json`. Harbor downloads that directory to
  `<trial-dir>/verifier/`.

For an oracle run (`-a oracle`), Harbor also copies `solution/` into the sandbox and runs
`solve.sh` in the agent phase; the verifier then grades the result the same way. `harbor run -c
<job-config>` runs every task of a run with the run id in the agent and verifier environments.

The base image warms the Go module and build caches; each task image checks out its older base
commit, truncates every history vector, clears the build cache, re-downloads the pinned module
versions, and proves the final build offline. The model API is reached from the host only; the
sandbox itself stays offline.

```mermaid
flowchart TB
  subgraph ws["developer workstation [Deployment Node: Linux, macOS, or WSL]"]
    subgraph processes["TraceBench [Deployment Node: OS processes]"]
      sample["<b>tracebench-sample</b><br/>[Container: Go]"]:::container
      loader["<b>tracebench-corpus</b><br/>[Container: Python, uv]"]:::container
      snap["<b>snapshot</b><br/>[Container: Go]"]:::container
    end
    subgraph files["data dirs [Deployment Node: filesystem]"]
      corpustree[("<b>corpus tree</b><br/>[Container: JSON, JSONL]<br/>corpus/index, dataset, dump")]:::container
      payload[("<b>task payloads</b><br/>[Container: JSON, JSONL]<br/>pr.json, prior-traces, repo-request")]:::container
      taskdir[("<b>Harbor tasks</b><br/>[Container: TOML, shell, Python]<br/>tasks/&lt;name&gt;")]:::container
      jobcfg[("<b>Harbor job config</b><br/>[Container: YAML or JSON]<br/>job-config.yaml")]:::container
      db[("<b>peasant database</b><br/>[Container: SQLite]<br/>peasant.db, read-only")]:::container
      clone[("<b>target git clone</b><br/>[Container: git working tree]<br/>commits and trees")]:::container
    end
    subgraph sandbox["task sandbox [Deployment Node: Docker or Podman]"]
      taskimg[("<b>task image</b><br/>[Container: FROM tracebench/task-runtime]")]:::container
      run["<b>task run</b><br/>[Container: environment,<br/>agent, verifier phases]"]:::container
    end
    harbor["<b>harbor CLI</b><br/>[Container: Python]"]:::external
  end

  gh["<b>GitHub</b><br/>[Software System, external]"]:::external
  hf["<b>HuggingFace Hub</b><br/>[Software System, external]"]:::external
  api["<b>model API</b><br/>[Software System, external]"]:::external

  sample -->|"reads sessions<br/>(SQLite, read-only)"| db
  sample -->|"lists pull requests<br/>(gh CLI, HTTPS)"| gh
  sample -->|"writes the corpus<br/>(files)"| corpustree
  sample -->|"fetches the dump<br/>(HTTPS)"| hf
  loader -->|"reads the dump<br/>(files)"| corpustree
  loader -->|"resolves boundaries<br/>(git CLI)"| clone
  loader -->|"writes payloads<br/>(files)"| payload
  loader -->|"writes tasks<br/>(files)"| taskdir
  loader -->|"writes the job config<br/>(files)"| jobcfg
  loader -->|"packs tree_commit history<br/>(git CLI, read-only)"| clone
  snap -->|"reads history<br/>(git CLI)"| clone
  harbor -->|"builds the task image<br/>(container runtime)"| taskimg
  harbor -->|"starts the task run<br/>(container runtime)"| run
  harbor -->|"reads the job config<br/>(files)"| jobcfg
  run -->|"reads uploaded task data<br/>(files)"| taskdir
  harbor -->|"model API calls use the<br/>host network (HTTPS)"| api

  style ws fill:none,stroke:#444,stroke-dasharray:6 4
  style processes fill:none,stroke:#444,stroke-dasharray:4 4
  style files fill:none,stroke:#444,stroke-dasharray:4 4
  style sandbox fill:none,stroke:#444,stroke-dasharray:4 4

  classDef person fill:#08427b,stroke:#052e56,color:#fff
  classDef system fill:#1168bd,stroke:#0b4884,color:#fff
  classDef container fill:#438dd5,stroke:#2e6295,color:#fff
  classDef component fill:#85bbf0,stroke:#5d82a8,color:#000
  classDef external fill:#999999,stroke:#6b6b6b,color:#fff
```

## Dynamic views

### Build the corpus

```mermaid
sequenceDiagram
  autonumber
  actor dev as benchmark engineer
  participant sample as tracebench-sample (Go)
  participant gh as GitHub
  participant db as peasant database
  participant corpus as corpus tree
  participant hf as HuggingFace Hub
  dev->>sample: tracebench-sample index
  sample->>gh: gh pr list --state merged (HTTPS)
  sample->>db: reads sessions, commits, lineage (SQL, read-only)
  sample->>sample: attributes sessions, closes over lineage
  sample->>corpus: writes corpus/index
  dev->>sample: tracebench-sample sample
  sample->>sample: stratified selection, split assignment
  sample->>db: opens raw or exported transcripts (SQL, peasant export)
  sample->>corpus: writes corpus/dataset
  dev->>sample: tracebench-sample dump
  sample->>db: reads artifacts, exports full-content captures (peasant export)
  sample->>sample: redacts at the standard level (redact engine)
  sample->>corpus: writes metadata.jsonl, transcripts/, indexes, manifest.json
  dev->>hf: publishes the dump (HuggingFace tooling)
```

### Generate a task from the corpus

```mermaid
sequenceDiagram
  autonumber
  actor dev as benchmark engineer
  participant fetch as tracebench-sample fetch
  participant hf as HuggingFace Hub
  participant corpus as corpus tree
  participant loader as tracebench-corpus pipeline
  participant git as target git clone
  participant payload as task payload
  participant task as Harbor task
  participant job as Harbor job config
  dev->>fetch: tracebench-sample fetch --dest data/tracebench
  fetch->>hf: downloads manifest, metadata, indexes, transcripts (HTTPS)
  fetch->>corpus: verifies content hashes, writes the dump
  dev->>loader: tracebench-corpus pipeline --prs --repo-dir --index --dest
  loop each pull request
    loader->>corpus: reads traces, metadata, transcripts
    loader->>git: resolves merge_commit^ and ancestry (git CLI)
    loader->>loader: selects prior traces, excludes own sessions, cuts at the boundary
    loader->>git: ls-tree and blobs at merge_commit matching the test patterns
    loader->>payload: writes pr.json, prior-traces/, tests/, test-manifest.json, repo-request.json
    loader->>git: pack-objects --revs tree_commit into a fresh repo (full ancestry, source read-only)
    loader->>loader: asserts HEAD tree == tree_commit^{tree}, source-equal count, not shallow, no remotes, fix absent
    loader->>payload: moves repo/ into the payload
    loader->>git: git diff tree_commit merge_commit, applies in a temporary worktree
    loader->>loader: asserts the applied tree == merge_commit^{tree}
    loader->>payload: writes solution/oracle.patch, solve.sh, the task.json oracle block
    loader->>task: writes task.toml, instruction.md, environment/, tests/, solution/
  end
  loader->>job: writes job-config.yaml for the tasks that were built
  loader-->>dev: one line per task (ok, or the failed part)
```

### Run a task

```mermaid
sequenceDiagram
  autonumber
  actor dev as benchmark engineer
  participant harbor as Harbor CLI
  participant img as task image
  participant sandbox as task sandbox
  participant api as model API
  dev->>harbor: harbor run -p tasks/name -a agent -e podman (or -c job-config.yaml)
  harbor->>img: builds FROM tracebench/task-runtime (environment phase, public)
  harbor->>sandbox: starts the task, uploads environment/ into the workdir
  alt oracle agent
    harbor->>sandbox: copies solution/, runs solve.sh (git apply, build)
  else model agent
    harbor->>api: model API calls use the host network (HTTPS)
    sandbox->>sandbox: agent phase: works on workdir/repo (no network)
  end
  harbor->>sandbox: verifier phase: copies tests/ to /tests, runs test.sh (no network)
  sandbox->>sandbox: removes pre-PR tests, overlays tests/golden/, runs the test command
  sandbox->>sandbox: joins go test -json to test-manifest.json, counts cases
  sandbox->>harbor: writes reward.txt (passed / total) and test-results.json
  harbor-->>dev: result and jobs/ artifacts
```

### Snapshot a repository at a cutoff

```mermaid
sequenceDiagram
  autonumber
  actor dev as benchmark engineer
  participant snap as snapshot (Go)
  participant peasant as peasant CLI
  participant git as target git clone
  participant out as snapshot output
  alt pr cutoff
    dev->>snap: go run ./cmd/snapshot --repo --cutoff-type pr --pr N
    snap->>peasant: peasant pr show N --format json (exec)
    peasant-->>snap: PR start timestamp
    snap->>git: git log --before, git ls-tree (git CLI)
    snap->>snap: newest satisfying commit owns the tree
  else commit cutoff
    dev->>snap: go run ./cmd/snapshot --repo --cutoff-type commit --commit SHA
    snap->>git: git log (ancestry of SHA), git ls-tree SHA (git CLI)
    snap->>snap: the pinned commit owns the tree, independent of HEAD
  end
  snap->>snap: repo_sha, tree_sha, manifest_sha256
  snap->>out: history.json (and repo/ with --materialize)
```

## Call sequences

Solid arrows are calls. Dashed arrows are returns. An arrow from a participant to itself is a
step inside that participant.

### Index and sample

Entry: `cmd/tracebench-sample/main.go` (`runIndex`, `sampleAndCollect`). Index: `internal/prindex`.
Sampling: `internal/sampler`. Collection: `internal/collector`.

```mermaid
sequenceDiagram
  participant cmd as cmd/tracebench-sample
  participant pidx as prindex
  participant store as peasantstore
  participant gh as gh CLI
  participant sel as sampler
  participant col as collector
  cmd->>gh: FetchMergedPRs: gh pr list --json
  cmd->>store: LoadSessions, LoadSessionCommits, LoadSessionEdges
  cmd->>pidx: NewKeyExtractor, ResolveCommitPRs
  pidx->>gh: commits/{sha}/pulls for unresolved hashes
  cmd->>pidx: Attribute, BuildSessionRelations, BuildSummary
  cmd->>pidx: SaveIndex (corpus/index)
  cmd->>sel: Select(candidates, Config)
  sel-->>cmd: assignments per split
  cmd->>store: OpenTranscriptReader
  cmd->>col: Collect(bundles, Options)
  col->>store: Open per session (raw copy or export)
  col-->>cmd: dataset manifest (corpus/dataset)
```

### Task payload and skeleton

Entry: `loaders/tracebench_corpus/cli.py` (`_task`, `_skeleton`). Payload:
`loaders/tracebench_corpus/task.py`. Skeleton: `loaders/tracebench_corpus/skeleton.py`.

```mermaid
sequenceDiagram
  participant cli as tracebench_corpus.cli
  participant cor as Corpus
  participant tb as TaskBuilder
  participant git as target git clone
  participant sk as build_skeleton
  cli->>cor: load_corpus(path or repo)
  cli->>tb: TaskBuilder(corpus, pr_index, repo_dir).build(pr, dest)
  tb->>tb: boundary(): merge_commit^ or merged_at
  tb->>git: git rev-parse, git show -s --format=%cI
  tb->>cor: sessions_for_pr for each prior pull request
  tb->>tb: excludes own sessions, cuts sessions past the boundary
  tb->>tb: writes pr.json, prior-traces/, repo-request.json, task.json
  cli->>sk: build_skeleton(payload, dest)
  sk->>sk: task.toml, instruction.md, environment/, tests/, solution/
  sk->>sk: tests/test.sh, verifier-config.json, verifier.py
```

### Pipeline

Entry: `loaders/tracebench_corpus/cli.py` (`_pipeline`). Driver:
`loaders/tracebench_corpus/pipeline.py` (`run_pipeline`, `build_task`). Steps: `task.py`,
`golden.py`, `test_manifest.py`, `worktree.py`, `oracle.py`, `skeleton.py`. Commands:
`repository_spec.py`.

```mermaid
sequenceDiagram
  participant cli as tracebench_corpus.cli
  participant pl as run_pipeline
  participant bt as build_task
  participant tb as TaskBuilder
  participant wt as materialize_worktree
  participant orc as build_oracle
  participant sk as build_skeleton
  cli->>cli: read_pr_list(--prs), load_pr_index, load_repository_specs(--spec)
  cli->>pl: run_pipeline(corpus, pr_ids, dest, repo_dir, pr_index, specs, run_id)
  pl->>pl: new_run_id(label) when no --run-id
  loop each pull request
    pl->>bt: build_task(builder, pr_id, dest)
    bt->>bt: find_repository_spec(specs, repo)
    bt->>tb: build(pr_id, payloads/name) with materialize_tests
    tb->>tb: build_test_manifest, materialize_golden_tests
    bt->>wt: materialize_worktree(repo_dir, payload)
    wt->>wt: pack-objects ancestors of SHA, init, index-pack, reset --hard
    wt->>wt: HEAD tree == rev-parse tree_commit^{tree}, count == source count, no shallow, merge_commit absent
    bt->>orc: build_oracle(repo_dir, tree_commit, merge_commit, build_command)
    orc->>orc: git diff, worktree add --detach, apply --check, write-tree
    bt->>bt: write_oracle(payload, oracle)
    bt->>sk: build_skeleton(payload, tasks/name, test_command)
    bt->>bt: checks REQUIRED_TASK_PARTS
    bt-->>pl: TaskResult(ok, or failed_part and error)
  end
  pl->>pl: job_config(run_id, built tasks, config)
  pl->>pl: write_job_config(dest)
  pl-->>cli: PipelineResult
```

### Verifier

Entry: `tests/test.sh` in the task, which runs `python3 /tests/verifier.py run`. Source:
`loaders/tracebench_corpus/verifier.py` (`run`, `parse_go_test_json`, `grade`,
`write_results`).

```mermaid
sequenceDiagram
  participant sh as tests/test.sh
  participant run as verifier.run
  participant repo as /workdir/repo
  participant go as test command
  participant gr as grade
  participant logs as /logs/verifier
  sh->>run: python3 /tests/verifier.py run
  run->>run: loads test-manifest.json and verifier-config.json
  run->>repo: remove_test_files(remove_regexes)
  run->>repo: overlay(/tests/golden)
  run->>go: go test -json -count=1 ./... (with a timeout)
  go-->>run: JSON stream and exit code
  run->>gr: grade(manifest, stream, module path)
  gr->>gr: parse_go_test_json: top-level tests, final action
  gr->>gr: joins on (package_dir, name), sets the outcome: pass, fail, skip, or missing
  gr-->>run: reward = passed / total, or 0 on a fail-closed reason
  run->>logs: write_results: reward.txt and test-results.json
```

### Snapshot

Entry: `snapshot/cmd/snapshot/main.go`. Assembly: `snapshot/api.go`. Cutoff:
`snapshot/cutoff.go`. Git: `snapshot/repo.go`. Traces: `snapshot/trace.go`.

```mermaid
sequenceDiagram
  participant cli as cmd/snapshot
  participant api as snapshot.SnapshotRepo
  participant cut as Cutoff.Resolve
  participant pc as BinaryPeasantClient
  participant repo as RepoSnapshotter
  participant tr as TraceProvider
  cli->>cut: ByPR(n).Resolve(client, override)
  cut->>pc: PRStart(n)
  pc->>pc: peasant pr show n --format json (exec)
  cli->>api: SnapshotRepo(repo, cutoff, opts)
  api->>repo: ListHistory (git log --before)
  api->>repo: ListTree (git ls-tree -r)
  api->>tr: ListFiles(cutoff)
  api->>api: ManifestHash (canonical JSON, sha256)
  cli->>api: WriteSnapshot or Materialize (git archive | tar)
```

## Package map

| Path | Role | Main callers |
|---|---|---|
| `cmd/tracebench-sample` | Command tree: index, sample, run, dump, fetch. | user |
| `internal/corpus` | Shared data model: pull requests, sessions, trace links, session artifacts. | all Go packages |
| `internal/prindex` | gh pull-request listing, session keys, attribution, commit mapping, lineage relations, index persistence. | `index`, `sample` |
| `internal/peasantstore` | Read-only SQLite access: sessions, commits, lineage edges, artifacts, pulled transcripts, transcript exports. | prindex, collector, dump |
| `internal/sampler` | Time- and size-stratified selection and group-aware split assignment. | `sample` |
| `internal/collector` | Bundle materialization: transcript copies or exports, per-PR records, dataset manifest. | `sample` |
| `internal/dump` | Dump writer: schema records, redaction, indexes, dump manifest. | `dump` |
| `internal/fetch` | HuggingFace download and content-hash verification. | `fetch` |
| `loaders/tracebench_corpus` | Python loader: corpus, bundles, task payloads, golden suite, case catalog, secure worktree, oracle, repository adaptation spec, skeletons, verifier, pipeline driver and job config, target configurations. | task authors, Harbor |
| `snapshot` | Go module: cutoff resolution (date, PR start, or exact commit), repo tree and history materialization, `tree_sha`, trace collection. | snapshot users |
| `tasks/_base` | Shared base-image Dockerfile with warmed Go caches, and the task-runtime image that strips the clone and build cache. | task image builds |
| `tasks/<name>` | Harbor task definitions: instruction, task.toml, environment, solution, tests. | `harbor run` |
| `scripts/verify-issue-*.sh` | Acceptance checks for the containerized codebase and the snapshot API. | developers |
