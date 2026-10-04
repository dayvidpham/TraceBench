# How other benchmarks score coding agents

We looked at how three well-known benchmarks run and score coding agents, so we can decide how
TraceBench should score its own runs:

- **Terminal-Bench** (versions 2.0 and 4.0)
- **HarnessTax**, a study by UC Berkeley and Arena
- **SWE-bench Verified**, by OpenAI

This is a plain-words summary plus what we think we should copy. All numbers come from the sources
at the bottom. A few that need a second look are listed under "Open questions".

## TL;DR

- **Pass/fail on the final code.** Everyone grades an attempt by running tests on the end result.
  Nobody grades the agent's steps along the way.
- **Two buckets of tests, from SWE-bench.** Tests the PR added have to go from failing to passing.
  Tests that already passed have to keep passing. This fits us best, because our tasks come from
  real PRs too.
- **Run each task more than once.** Agents are random, so one try is mostly luck. HarnessTax did 3
  tries per task.
- **With ~30 tasks, the error bars are big:** roughly ±15 percentage points. Small differences
  between runs don't mean anything.
- **Track cost, not just success.** HarnessTax found that swapping the harness barely changed
  success but changed cost by up to 5×. Traces might work the same way.
- **Crashes and timeouts count as fails.** Don't quietly drop them.

## The three benchmarks in a minute

**Terminal-Bench.** Hard, realistic tasks done inside a Linux terminal: sysadmin work, data work and
coding. Version 2.0 had 89 tasks. Version 4.0 trimmed that to 66 by dropping tasks every model had
started to solve and fixing others. It runs on Harbor, the same framework we use, so our task layout
is basically theirs.

**HarnessTax.** It asks one question: does the wrapper around the model (the "harness") matter?
They ran 7 models in 3 harnesses (Claude Code, Codex CLI and Pi), which makes 21 pairs. Every pair
got the same 30 random tasks from SWE-bench Lite and the same 30 from Terminal-Bench 2.0, with 3
tries per task.

**SWE-bench Verified.** 500 tasks built from real GitHub issues and PRs in 12 Python repos. OpenAI
had 93 engineers screen 1,699 tasks, and they threw out 68.3% for being vague or having unfair
tests. Scores about doubled on the cleaned-up set: GPT-4o went from 16% to 33.2%. It's the closest
thing to TraceBench, since we also build tasks from real PRs.

## Side by side

| | Terminal-Bench | HarnessTax | SWE-bench Verified |
|---|---|---|---|
| **What a task is** | A container, instructions, tests and a reference solution | 30 tasks each borrowed from SWE-bench Lite and Terminal-Bench 2.0 | A real GitHub issue, the repo at that point, and hidden tests |
| **When a try counts as solved** | All tests pass on the final container | The original benchmark's checker says pass | Every fail-to-pass and pass-to-pass test passes |
| **What they report** | Solve rate, tokens, time, turns | Success rate, cost per run, cost per solve, starting prompt size, turns | Solve rate, also split by how long a human would need (under 15 min, 15 min–1 h, 1–4 h, over 4 h) |
| **Tries per task** | 5 or more | 3 | Usually 1 |
| **Error bars** | 95% intervals | 95% intervals from 10,000 bootstrap resamples | Standard binomial error bars |
| **Crashes and timeouts** | Count as fails | Count as fails, and their cost still counts. 100-turn cap. | Count as unsolved |
| **Cost tracking** | Input and output tokens, runtime, API cost per try | Input, output, cached and starting-prompt tokens. Dollars from one fixed price list (Sept 1, 2026). | Tokens and steps per try, depending on the agent |
| **Keeping it honest** | Reference solution must pass, a do-nothing run must fail. Expert review, LLM checks, a "cheater" agent and a canary string. | No outside network; web and hosted tools turned off | Humans screened out vague tasks and unfair tests |
| **Fair comparisons** | Same neutral agent (Terminus 2) for every model | Same 30 tasks, same 100-turn cap and effort settings for every pair | Same 500 tasks and setups for everyone |

A canary string is a secret marker hidden in the task files. If it ever shows up in a model's
output, you know the tasks leaked into its training data.

## HarnessTax numbers worth knowing

- **Success barely moved.** Swapping harnesses changed it by about ±2 points on SWE-bench Lite and
  ±5 points on Terminal-Bench.
- **Cost moved a lot, up to 5×.** Across shared models, Claude Code cost about 2× as much as Pi on
  SWE-bench Lite and 1.5× on Terminal-Bench. For example, on SWE-bench Lite, Claude Fable 5 solved
  97.8% in Claude Code at $1.33 a run, and 96.7% in Pi at $0.67.
- **Small harnesses can win.** Pi has just 4 tools: read, write, edit and bash. Claude Code's
  starting prompt was over 10× bigger, about 77k characters versus 2.8k.
- **A model's "home" harness isn't always its best.** In 9 of 12 comparisons, a model did best in
  someone else's harness.

**Why we care:** if the harness changes cost much more than success, traces might too. So we should
measure both.

## How agents usually fail

These come from the Terminal-Bench paper and the SWE-bench Verified write-up.

- **Bad commands are the biggest bucket.** They're 35.1% of about 3,800 failed commands, and
  "command not found" alone is 24.1%.
- **Weak self-checks.** Agents stop early, skip running the tests, or even edit the tests to force a
  pass.
- **Hidden underspecification.** The task needs an exact name or message that the instructions
  never mention.

**Why we care:**
- PR descriptions can be vague, and a PR's tests can depend on exact names the agent can't guess.
  We should screen tasks for this.
- We should always put the real test files back before grading, so editing the tests can't win.

## What TraceBench should do

**1. What counts as solved**
- **Adopt:** pass/fail from running the tests on the final code.
- **Adapt:** also save pass/fail for every single test, so the report can show what broke.
- **Skip:** grading the agent's steps along the way.

**2. Added tests vs. existing tests**
- **Adopt:** SWE-bench's two buckets.
  - The tests the PR added ("fail-to-pass") ask: did the agent build the thing?
  - The tests that already passed ("pass-to-pass") ask: did it break anything?
- **Adapt:** solved means 100% of both buckets pass. Also report each bucket's pass rate as partial
  credit, because an agent that does nothing still passes almost all the old tests.
- **Watch out for Go:** if a new test doesn't compile against the agent's code, the whole package
  fails to build, and every test in it fails. Record "build failed" separately, so it doesn't look
  like the agent broke 100 tests.

**3. Tries per task**
- **Adopt:** 3 tries per task, like HarnessTax. The score is the average over tries, then over
  tasks.
- **Skip:** one try per task. It's too noisy.

**4. Error bars with ~30 tasks**
- **Adopt:** 95% intervals from 10,000 bootstrap resamples.
  - Re-pick the 30 task scores at random, with replacement, and recompute the average.
  - Do that 10,000 times.
  - Keep the middle 95% of those averages.
- **Adapt:** "with traces" and "without traces" run on the same tasks, so compare them task by task
  (a paired comparison). That gives tighter error bars than comparing two averages.
- **Heads-up:** with 30 tasks, the bars are about ±15 points. A 5-point gap is noise.

**5. Cost**
- **Adopt:**
  - Record input, output, cached and starting-prompt tokens.
  - Turn tokens into dollars with one fixed price list, and write down its date.
  - Report cost per solve: average cost ÷ success rate.
- **Note:** Harbor already saves input, output and cached tokens plus cost for each run, but only if
  the harness adapter fills them in.

**6. Crashes and timeouts**
- **Adopt:** count them as fails and keep their cost. Don't drop them.
- **Adapt:** give every run the same limit, either a time limit or a turn cap. HarnessTax used 100
  turns. Our tasks use 10 minutes right now.

**7. Checking tasks before we use them**
- **Adopt:** basic checks when we build a task. Each task needs the PR, its acceptance criteria,
  both test lists and the traces.
- **Adapt:** run two automatic checks on every task. If either one goes the wrong way, the task is
  broken.
  - The real PR change, applied to the snapshot, must pass the tests.
  - The untouched snapshot must fail the new tests.
- **Skip:** PRs with vague descriptions or flaky tests.

**8. What every result record holds**
- **Which task and run:** task id, run type (no traces, prior sessions, or with the answer) and try
  number.
- **Setup:** model, plus harness name and version.
- **Tests:** passed tests, failed tests, pass counts for each bucket, and build failures.
- **Cost and time:** tokens, cost and time.
- **Status:** passed, failed, crashed or timed out.

The run type matters most. Without it, we can't compare "with traces" against "without".

## Open questions

- **Terminal-Bench file names.** Our notes list them as `task.yaml`, `run-tests.sh` and
  `solution.sh`, which looks like the older layout. Since 2.0 runs on Harbor, they're probably
  `task.toml`, `instruction.md`, `tests/test.sh` and `solution/solve.sh`, same as ours. Worth a
  quick check.
- **Time limits.** Terminal-Bench 4.0 reportedly gives every agent a flat 8-hour limit, and our
  tasks use 10 minutes. What do we want? Longer limits cost more.
- **Turn cap.** Can OpenCode cap the number of turns, like HarnessTax's 100?
- **Starting prompt size.** How do we measure it for OpenCode? Harbor doesn't save it directly.
- **Task count.** How many of our ~30 PRs will pass the task checks? Every dropped task makes the
  error bars wider.
- **Sources disagree.** For Terminal-Bench 4.0, they say either 19 or 20 tasks were patched.

## Sources

- Terminal-Bench 2.0 paper: <https://arxiv.org/abs/2601.11868>
- Terminal-Bench 4.0 announcement: <https://www.tbench.ai/news/terminal-bench-4-0>
- Terminal-Bench v4.0.0 release notes: <https://github.com/harbor-framework/terminal-bench/releases/tag/v4.0.0>
- Terminal-Bench 2.0 leaderboard: <https://snorkel.ai/leaderboard/terminal-bench-2-0/>
- HarnessTax: <https://harnesstax.github.io/>
- Arena's HarnessTax write-up: <https://arena.ai/blog/coding-agents-harness-tax>
- Digital Applied's HarnessTax write-up: <https://www.digitalapplied.com/blog/coding-agent-harness-cost-harnesstax-same-model-5x>
- SWE-bench Verified: <https://openai.com/index/introducing-swe-bench-verified/>
