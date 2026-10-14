# CodeLoom paired coding benchmark

10 tasks × two existing systems = **20 independent measured attempts**: 3 light, 3 medium and 4 heavy. The baseline is pinned **mini-SWE-agent 2.4.6**, using its native controller, Docker environment and LiteLLM tool-calling backend. This repository does not implement another coding agent. Engine runs its native orchestrator, children, tree-sitter and LSP. The optional external classifier is off in both arms.

## Current status

The owner authorized running the harness on Railway on 2026-10-14. Official paid commands remain held until the requested summary, certification, native compatibility, pricing, calibration and preregistration. The owner subsequently authorized actual-spend stopping with a 3x per-call estimate gate; the $50 allocation is now a target that final calls may overshoot.

The initial native compatibility check passes: both adapters preserve reasoning history and explicitly send the same High effort, including an Engine child. Railway rebuilds and repeats this check before calibration. `reports/compatibility.json` contains synthetic evidence, not coding scores. The live campaign state is at https://benchmark-monitor-production.up.railway.app/ and is authoritative for current progress.

## Railway execution

The worker runs in a Docker-enabled x86-64 Railway sandbox with idle destruction disabled. `cloud/worker.py` runs offline certification, compatibility, four paid development attempts, commits setup evidence, preregisters and pushes the timestamped tag, then runs measurement. Any failed gate stops the campaign. After offline setup, the supervisor waits for `.cache/cloud/paid-start-authorized`; the operator creates this marker only after delivering the requested owner-facing summary, before any paid calibration. `--resume-setup` permits CPU-only preparation after a fully settled unscored smoke test; it refuses active/unknown bills or existing official calibration/measurement trials. Starting it twice is refused; interrupted paid trials require reconciliation before resumption.

The `cloud/` Dockerfile deploys the separate monitoring service. Mount a Railway volume at `/data` and set a randomly generated `MONITOR_UPLOAD_TOKEN`; never commit this value. The worker needs the same token, `MONITOR_URL`, `OPENROUTER_API_KEY`, and a host-only Git credential helper for preregistration publication. Agents receive only broker tokens, never deployment credentials.

Public snapshots stream over `/api/events` (SSE) and are available at `/api/status`. The existing importer produces all summary, setup, patches and inspector views; only sealed evidence is published, atomically, after each completion. Calibration recordings remain labeled development and do not enter measurement totals. Private authenticated `/api/backup` holds a consistent SQLite backup and full journals for recovery; it is never under the public artifact root. Publication retries transient errors and the final snapshot remains available after the worker exits. Railway infrastructure costs are separate from the inference ceiling. The sandbox remains billable until explicitly destroyed after retaining its final checkpoint/backup.

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
| TinyDB | Cache invalidation (light) | Storage flush API (medium) | — | — |
| python-dotenv | — | Strict loading + CLI (heavy) | — | Selected value copies (light) |
| p-queue | Matching priority IDs (medium) | Atomic bulk priorities (heavy) | — | Settled batches (heavy) |
| chi | Pooled context reset (light) | Immutable bulk headers (heavy) | Route/context lifecycle (medium) | — |

Selection and scope rationale are pinned in [selection.yaml](suites/v1/selection.yaml), before any scored outputs. Light means a localized repair or tests for one helper; medium involves lifecycle tracing or several cooperating files; heavy emphasizes API/CLI propagation, queue atomicity, ownership or deterministic async tests. These labels are relative to this small corpus, not measured difficulty or large-repository SWE-bench equivalence. Categories are deliberately unequal (3 bugs, 4 cross-file features, 1 navigation, 2 test-generation tasks); avoid conclusions from tiny category subsets.

`B`, `X`, `N`, `T` mean bug, cross-file, navigation and test generation. There are no hidden B1/B2 leftovers. All three selected bugs are **fabricated regressions**, labeled in their manifests; upstream tests remain visible even when they expose the bug. No upstream maintainer is contacted and no PR is opened. Each task's source is a fresh local Git repository with a single starting commit and no upstream remote/history. Trusted upstream file symlinks are materialized as regular files with the same contents before that snapshot; agent-created links are rejected.

Bug/features require private behavior checks **and** trusted upstream regressions to pass. Existing tests/configuration cannot be edited. Navigation uses public JSON schema plus a runnable format validator, and hidden symbol/edge/claim checks. Public claim choices avoid grading arbitrary prose; expected relationships remain hidden. References and rubric equivalents were authored with Codex assistance and checked against pinned source and deterministic tests. No independent human review has taken place, and no measured DeepSeek output is used to tune accepted answers.

Test generation supplies a correct newly added feature with reference tests withheld (explicitly disclosed in the brief), preserving all upstream tests. Tests must pass three times on correct code and stably kill at least 80% of five valid mutants, with the same behavioral assertion failing on all three mutant runs. Import/build/collection failures do not count as kills. Certification confirms these mutants survive the trusted upstream suite; Python, Node/AVA and Go have separate parsers. Ky candidates were excluded before freezing after their upstream browser/timing checks failed under emulation; `reports/excluded-ky.json` records this. The original 16-task pool is retained; six fixtures are unselected reserves in this smaller study.

Only changes on the main `/task` checkout are submitted, including commits and allowed untracked files. Child branches are submitted only after native settlement merges them. The controller answers settlement prompts `merge` and continuation prompts `continue`; it waits for the native idle barrier to include queued reports and child settlements. The filesystem is sealed before a clean, separate evaluator reconstructs allowed changes. It never trusts the agent's installed tools or Git hooks.

## Model and budget

Generator: `deepseek/deepseek-v4-pro-0813`, thinking enabled at **High** effort, fixed provider `baidu/fp8`, no provider fallback, same model/effort for every Engine child and mini-SWE-agent. High is the model’s advertised default; its captured metadata supports `low`, `high` and `max`, so generic unsupported efforts are rejected. Both native controllers must send the agreed model/effort; the broker rejects mismatches and preserves opaque histories. The shared 32,768 combined-output ceiling remains unchanged.

Conservative admission rates are **$1.32/M input and $3.96/M output**, 4.4× the old rates. The captured promotional endpoint rates are $0.20196/$0.60588; the budget does not depend on the promotion. Reasoning and visible output are billed once together, and all child requests and replayed inputs count. [Cost analysis](reports/cost-analysis.md) gives token-sensitivity scenarios, not measured forecasts: the weighted 3/3/4 request-volume scenario costs about $26.33 for 20 attempts at conservative rates. Doubling heavy request counts exceeds the $4 cap. No High-vs-Max savings or quality improvement is claimed without paid calibration.

Refresh the read-only analysis with `./scripts/benchmark cost-analysis`. Versioned settings and model metadata are in `configs/core.yaml` and `configs/model.json`; dependency pins are in `uv.lock` and `engine-requirements.lock`.

| Allocation | Actual-spend stopping target |
| --- | ---: |
| Development/calibration (four $1.50 runs + probes) | $8 |
| 20 measured attempts (shared envelope) | $32 |
| Whole-task infrastructure recovery | $5 |
| OpenRouter fees / rounding buffer | $5 |
| Total | **$50** |

Individual attempt spending targets are **$1 light, $2 medium and $4 heavy**, identical for both systems on each task. A call is rejected if its conservative estimate is strictly above **3x the full trial target** ($3/$6/$12), not 3x remaining headroom. Otherwise, while actual provider charges remain below trial, stage and campaign targets, it is admitted even when its estimate exceeds remaining headroom. After its bill settles, no next call is admitted if actual spending has reached a target. The final admitted call may overshoot; this policy intentionally does not guarantee a $50 hard ceiling. Shared stage/campaign targets remain $8/$32/$5 and $45 inference plus $5 fees/buffer. Pair admission can leave scheduled tasks unrun; allocations are not guaranteed.

The host-only broker owns the real key and durable SQLite ledger. Conservative byte-input/full-output estimates remain recorded, but actual provider usage controls budget stopping. Engine children queue behind any in-flight inference in the same trial so they cannot multiply a final-call overshoot before the bill arrives; the other benchmark arm can still run concurrently. Agents receive only opaque per-attempt tokens. Unknown bills retain their full reservations; new paid calls stop until reconciled. A charge above the conservative request bound or wrong provider/model still halts admission. Policy and targets cannot silently change on resume; explicit migration of the settled development-only ledger preserves every historical charge. A campaign binds measurement to one run directory; renaming a run cannot create another set of primary attempts. The campaign ledger is `.cache/campaign/ledger.sqlite`; do not delete it to reset spent budget. `call_cost_limit` records distinguish expensive-call refusal from `budget_exhausted` actual stopping.

Each task’s two systems run concurrently in separate Docker containers; one matched task pair is admitted at a time. Calibration uses the same two-attempt parallelism. Fixture grading uses two workers after serialized image preparation. Execution never mounts the Docker socket or host home. Mini's native controller sits on the host and uses only the attempt token; its shell is isolated in Docker. The whole Engine process, children, worktrees and LSP live in its task container. An internal network offers one fixed inference relay, with no general Internet access. The agent-visible build context excludes the evaluator and references. Target libraries resolve to the task checkout, not separately installed fixed copies.

Calibration must complete both representative tasks in both systems without a budget or call-estimate stop. It uses $1.50 actual-spend targets per trial, with $4.50 call gates. It records token throughput and first-byte latency, and sets the shared watchdog to `max(60 minutes, 2 × longest completed calibration + 2 minutes)`. Request/stream idle timeouts are shared and derived from the slowest observed generation rate and longest first-byte latency, with a twofold margin and the task watchdog as the ceiling. If budget stops or integration issues invalidate calibration, measurement stops. A watchdog is a guard, not a timed-task performance target.

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

## Static website publication

The main homelab site intentionally has no live Railway dependency. The live monitor is separate. Once the selected campaign is finished, run `./scripts/benchmark import-site --run-dir runs/railway-v1 --pages ../pages` to generate static summaries, charts, run indexes, inspector pages and downloads. Import calibration separately into a temporary pages directory if its recordings are also wanted; they never enter scored totals. Build/push `iresharma/codeloom-web:latest` for AMD64 and ARM64, then restart `deployment/codeloom-web` in namespace `iresharma`. The homelab Kubernetes API is LAN-only; a Railway worker cannot directly restart it.

The authorized smoke test is `cloud/smoke.py`, using `suites/smoke/tasks/dev-smoke-python.yaml`, outside both v1 and calibration suites. It runs two arms concurrently at $0.50 each, sharing the development ledger. Every failure is retained. Official calibration/measurement stay behind the summary marker.
