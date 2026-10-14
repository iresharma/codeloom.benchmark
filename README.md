# CodeLoom paired coding benchmark

16 tasks × two existing systems = **32 independent measured attempts**. The baseline is pinned **mini-SWE-agent 2.4.6**, using its native controller, Docker environment and LiteLLM tool-calling backend. This repository does not implement another coding agent. Engine runs its native orchestrator, children, tree-sitter and LSP. The optional external classifier is off in both arms.

## Current status

**Paid calibration and measurement are paused at the owner's request pending review of the updated benchmark integration.** Do not start paid runs until the updated adapters and settings have been reviewed and the owner explicitly authorizes them.

**No paid or measured model calls have run.** The updated native compatibility check passes: both adapters preserve reasoning history and explicitly send the same High effort, including an Engine child. `reports/compatibility.json` contains synthetic evidence, not coding scores. The previous fixture certification is archived and marked stale; recertification, live development calibration and clean preregistration remain required. `paid_runs_authorized: false` stops paid commands before credentials are read.

Fixture certification and website output are separate from scored outcomes. `reports/certification.json` records every task's actual certification status. `reports/v1-not-run/` is a truthful empty results report, not fabricated performance data.

## Commands

Requires Docker, Git, Python 3.12 and [uv](https://docs.astral.sh/uv/). Docker must have enough disk for four repository images; the experiment pins `linux/amd64` and records ARM64 emulation on this Mac. The engine checkout is expected at `../engine`; use `--engine /absolute/path` otherwise.

One command to attempt the frozen protocol (with mandatory gates):

```sh
./scripts/benchmark run --run-dir runs/v1
```

At this revision this command stops at the explicit paid-run hold, before any inference spending. A successful study requires the following setup stages to pass first:

```sh
./scripts/benchmark validate
./scripts/benchmark certify
./scripts/benchmark compatibility
./scripts/benchmark calibrate --run-dir runs/calibration
./scripts/benchmark preregister
./scripts/benchmark run --run-dir runs/v1
```

`certify` constructs pinned source snapshots, builds agent-visible images and runs references/no-ops/upstream regressions/held-out mutants. Certification is CPU-only and reuses a certified task only when its source recipe, private oracle, grading code, dependency locks and runtime image still match. Missing images or changed inputs force rechecking. `calibrate` uses two separate development features (Python and TypeScript), one run per system. Commit the implementation and certification before `preregister`; this writes the locked schedule, commits it and creates an annotated timestamped Git tag before measured inference. The runner requires a clean frozen checkout. Push the preregistration tag before starting a public study.

Guaranteed **offline report reproduction**, without credentials or network calls:

```sh
./scripts/benchmark report --run-dir reports/v1-not-run
```

Rebuild the results-page data and public downloads:

```sh
./scripts/benchmark import-site --run-dir reports/v1-not-run --pages ../pages
```

This explicitly imports the selected run; it never silently promotes a partial run as the latest completed result. The existing repository case-study URLs remain available beneath the benchmark overview.

The importer also builds `runs.json`, per-attempt `view.json`, and the website's run index. These are offline projections of sealed evidence, not extra model calls. Each run opens at `/engine/results/runs/<run>/<attempt>` with filterable model/tool timelines, agent-parent relationships, request timing and effort, grader output, and submitted patches. Engine results are labeled as tool-output previews; unavailable traces and unknown charges remain unavailable. The synthetic checks have their own labels and never enter scored totals. Historical case studies only have agent summaries and published tool counts; their missing individual call logs are not reconstructed.

## Corpus and grading

| Repository | Bug | Cross-file | Navigation | Test generation |
| --- | --- | --- | --- | --- |
| TinyDB | Cache invalidation | Storage flush API | Search/update/cache lifecycle | Conditional cache flush |
| python-dotenv | Interpolation precedence | Strict loading + CLI | Environment construction | Selected value copies |
| p-queue | Matching priority IDs | Atomic bulk priorities | Queue lifecycle | Settled batches |
| chi | Pooled context reset | Immutable bulk headers | Route/context lifecycle | Writer snapshots |

`B`, `X`, `N`, `T` mean bug, cross-file, navigation and test generation. There are no hidden B1/B2 leftovers. All four current bugs are **fabricated regressions**, labeled in their manifests; upstream tests remain visible even when they expose the bug. No upstream maintainer is contacted and no PR is opened. Each task's source is a fresh local Git repository with a single starting commit and no upstream remote/history. Trusted upstream file symlinks are materialized as regular files with the same contents before that snapshot; agent-created links are rejected.

Bug/features require private behavior checks **and** trusted upstream regressions to pass. Existing tests/configuration cannot be edited. Navigation uses public JSON schema plus a runnable format validator, and hidden symbol/edge/claim checks. Public claim choices avoid grading arbitrary prose; expected relationships remain hidden. References and rubric equivalents were authored with Codex assistance and checked against pinned source and deterministic tests. No independent human review has taken place, and no measured DeepSeek output is used to tune accepted answers.

Test generation supplies a correct newly added feature with reference tests withheld (explicitly disclosed in the brief), preserving all upstream tests. Tests must pass three times on correct code and stably kill at least 80% of five valid mutants, with the same behavioral assertion failing on all three mutant runs. Import/build/collection failures do not count as kills. Certification confirms these mutants survive the trusted upstream suite; Python, Node/AVA and Go have separate parsers. Ky candidates were excluded before freezing after their upstream browser/timing checks failed under emulation; `reports/excluded-ky.json` records this. Four named reserve tasks preserve the category balance.

Only changes on the main `/task` checkout are submitted, including commits and allowed untracked files. Child branches are submitted only after native settlement merges them. The controller answers settlement prompts `merge` and continuation prompts `continue`; it waits for the native idle barrier to include queued reports and child settlements. The filesystem is sealed before a clean, separate evaluator reconstructs allowed changes. It never trusts the agent's installed tools or Git hooks.

## Model and budget

Generator: `deepseek/deepseek-v4-pro-0813`, thinking enabled at **High** effort, fixed provider `baidu/fp8`, no provider fallback, same model/effort for every Engine child and mini-SWE-agent. High is the model’s advertised default; its captured metadata supports `low`, `high` and `max`, so generic unsupported efforts are rejected. Both native controllers must send the agreed model/effort; the broker rejects mismatches and preserves opaque histories. The shared 32,768 combined-output ceiling remains unchanged.

Conservative admission rates are **$1.32/M input and $3.96/M output**, 4.4× the old rates. The captured promotional endpoint rates are $0.20196/$0.60588; the budget does not depend on the promotion. Reasoning and visible output are billed once together, and all child requests and replayed inputs count. [Cost analysis](reports/cost-analysis.md) gives token-sensitivity scenarios, not measured forecasts: the moderate scenario costs $17.74 for 32 attempts at conservative rates, while the heavy scenario exceeds the $1 attempt cap. No High-vs-Max savings or quality improvement is claimed without paid calibration.

Refresh the read-only analysis with `./scripts/benchmark cost-analysis`. Versioned settings and model metadata are in `configs/core.yaml` and `configs/model.json`; dependency pins are in `uv.lock` and `engine-requirements.lock`.

| Allocation | Ceiling |
| --- | ---: |
| Development/calibration (four $1.50 runs + probes) | $8 |
| 32 measured attempts | $32 |
| Whole-task infrastructure recovery | $5 |
| OpenRouter fees / rounding buffer | $5 |
| Total | **$50** |

The host-only broker owns the real key and durable SQLite ledger. It reserves conservative input/output upper bounds transactionally before every request, covering concurrent children. Agents receive only opaque per-attempt tokens. Unknown bills retain their full reservations; new paid calls stop until reconciled. Any price overrun halts admission. Defaults cannot be increased when resuming a campaign. A campaign binds measurement to one run directory; renaming a run cannot create another set of primary attempts. The campaign ledger is `.cache/campaign/ledger.sqlite`; do not delete it to reset spent budget. Actual charges come from provider usage, not a second client-side estimate.

All agents run serially in Docker, never with the Docker socket or host home mounted. Mini's native controller sits on the host and uses only the attempt token; its shell is isolated in Docker. The whole Engine process, children, worktrees and LSP live in its task container. An internal network offers one fixed inference relay, with no general Internet access. The agent-visible build context excludes the evaluator and references. Target libraries resolve to the task checkout, not separately installed fixed copies.

Calibration must complete both representative tasks in both systems under its ceilings. It records token throughput and first-byte latency, and sets the shared watchdog to `max(60 minutes, 2 × longest completed calibration + 2 minutes)`. Request/stream idle timeouts are shared and derived from the slowest observed generation rate and longest first-byte latency, with a twofold margin and the task watchdog as the ceiling. If caps or integration issues invalidate calibration, measurement stops. A watchdog is a guard, not a timed-task performance target.

No ablations or repeats in v1. This estimates a paired whole-system difference; it cannot attribute gains to LSP, context strategies, the classifier or agent personalities. Tasks from one repository are correlated and this is an author-designed small corpus. There are no confidence intervals or reliability claims.

## Failure and reporting rules

Each scheduled task/system outcome has one vote. A confirmed provider/host infrastructure error can receive at most one fresh whole-task recovery attempt. The recovery outcome replaces the original outcome; all original charges and elapsed time remain in the totals. Agent crashes, budget refusals, invalid outputs and regressions are failures, never retry candidates. Unresolved infrastructure outcomes are excluded from the evaluated denominator, but remain in delivered/scheduled coverage.

An interrupted attempt stays visible even if its final result was never sealed: its bill and elapsed time remain unavailable, rather than being treated as zero. Unsealed logs are withheld from public export until revocation and sealing are verified.

Reports lead with Engine-only / mini-only / both-pass / both-fail counts and explicitly show unresolved/unrun pairs. Cost per success includes failures and retries. Regression counts are conservative command-level signals: trusted check commands that fail after passing on the starting snapshot. Starting bug failures are excluded; raw test outputs support finer manual analysis. Unknown provider charges are marked unavailable rather than presented as actual costs. Reports and downloadable patches/traces preserve failures alongside successes. `SHA256SUMS` verifies sealed artifacts. There are no model-as-judge grades or invented metrics.

Offline reports are deterministic for the same sealed artifacts. A new protocol run is reproducible; matching stochastic outputs, historical endpoint availability and promotional costs are not guaranteed. Raw broker request/response blobs and native traces are retained, with provider routing and first-byte/elapsed measurements. Export refuses credential markers and never copies the ledger or worker environment.

## Model selection sources

[SWE-bench leaderboard](https://www.swebench.com/) is supporting evidence for agentic coding, not a portable model ranking: scaffold and test set affect scores. [DeepSeek's model card](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro) and [OpenRouter model information](https://openrouter.ai/deepseek/deepseek-v4-pro-0813) motivated the quality/price choice. [DeepSeek thinking-mode documentation](https://api-docs.deepseek.com/guides/thinking_mode/) and [OpenRouter reasoning-state documentation](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens) define the native fidelity gate.

## Development

```sh
uv sync --frozen
uv run pytest -q
uv run ruff check .
```

The broker tests use a local synthetic provider and make no paid calls. Mutation/reference certification uses trusted, separate Docker evaluator containers. Synthetic adapter traces are labeled as integration checks and must never be included as measured attempts.
