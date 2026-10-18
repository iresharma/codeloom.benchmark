# CodeLoom paired coding benchmark

Local harness. Same tasks, same model, two systems: **mini-SWE-agent 2.4.6** and **Engine core**. Both run in isolated Docker containers on this machine. There is no Railway worker.

The published page at [codeloom.iresharma.com/engine/results](https://codeloom.iresharma.com/engine/results) is the earlier classic run. This tree can run those tasks again and the newer realistic suite, then write the same results files the site already imports.

## Suites

- **classic** (`suites/v1`): 10 tasks, 3 light / 3 medium / 4 heavy. Bug fix, cross-file, navigation, test generation. Caps $1 / $2 / $4.
- **realistic** (`suites/v2`): 15 tasks, all heavy ($4). Adds browser, terminal and research work on real upstream repos.

Hidden checks live under `evaluator/`. Agents never see them.

## Run on this laptop

Needs Docker, Git, Python 3.12, and the Engine checkout at `../engine`. Plug the charger in and leave the lid open. The runner calls `caffeinate` so the Mac stays awake on AC power.

```sh
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
export OPENROUTER_API_KEY=...
./scripts/benchmark prepare-runtime --engine ../engine
./scripts/benchmark run --suite classic --run-dir runs/classic --engine ../engine
./scripts/benchmark monitor
./scripts/benchmark report --run-dir runs/classic
./scripts/benchmark publish --run-dir runs/classic --pages ../pages
```

`--suite realistic` is the same runner. `--parallel 1` runs the two systems one after another if memory is tight. Closing the lid or rebooting is safe: finished `result.json` files are skipped.

Model: `deepseek/deepseek-v4-pro-0813`, provider `baidu/fp8`, reasoning high. The host proxy holds the real key. Task containers only reach that proxy.

Engine submissions come from `/task/.engine/worktrees/<agent_id>`. The runner keeps those worktrees and grades the one that changed. mini-SWE-agent edits `/task` directly.

## Commands

```sh
./scripts/benchmark tasks --suite classic
./scripts/benchmark run --suite classic --run-dir runs/classic --engine ../engine
./scripts/benchmark monitor
./scripts/benchmark report --run-dir runs/classic
./scripts/benchmark publish --run-dir runs/classic --pages ../pages
```

`monitor` opens http://127.0.0.1:8765. `--run-dir` is optional; the page can open any local run under `runs/`, `reports/`, or `tests/fixtures/`. Click an arm to inspect the agent stack, tool timeline, model calls, patch, grader checks and controller log. While an Engine container is still up, the inspector copies `/tmp/engine-events.jsonl` so the stack updates live.

`publish` copies `site/summary.json` into the pages repo as `benchmark.json` plus `/benchmarks/<run_id>/` evidence. It does not replace a live page until you point the site at that run.

## Development

```sh
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
.venv/bin/ruff check .
```
