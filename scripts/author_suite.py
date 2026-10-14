#!/usr/bin/env python3
"""Explicit, reviewable fixture authoring. Never run in an agent container."""

from pathlib import Path
import json
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codeloom_benchmark.fixtures import write_task
from codeloom_benchmark.artifacts import write_json
from codeloom_benchmark.manifests import ROOT

REPOS = {
    "tinydb": ("msiemens/tinydb", "python"),
    "dotenv": ("theskumar/python-dotenv", "python"),
    "pqueue": ("sindresorhus/p-queue", "typescript"),
    "ky": ("sindresorhus/ky", "typescript"),
    "chi": ("go-chi/chi", "go"),
}
DEFER = {"tinydb-N", "dotenv-T", "pqueue-B", "chi-X"}
TASKS = []


def edit(path, old, new):
    count = (
        (ROOT / ".cache/repos/tinydb/tinydb/storages.py").read_text().count(old)
        if path == "tinydb/storages.py"
        else 1
    )
    return {path: [{"old": old, "new": new, "count": count}]}


def merge(*parts):
    result = {}
    for p in parts:
        for key, value in p.items():
            if (
                key in result
                and isinstance(result[key], list)
                and isinstance(value, list)
            ):
                result[key].extend(value)
            else:
                result[key] = value
    return result


def add(repo, kind, title, prompt, setup=None, reference=None, oracle="", mutants=None):
    ident = repo + "-" + kind
    if ident in DEFER:
        return
    upstream, lang = REPOS[repo]
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT / ".cache/repos" / repo, text=True
    ).strip()
    category = {
        "B": "bug",
        "X": "cross-file",
        "N": "navigation",
        "T": "test-generation",
    }[kind]
    verify = {
        "tinydb": ["python -m pytest -o addopts= -q tests"],
        "dotenv": ["python -m pytest -q tests"],
        "pqueue": [
            "npm run build",
            "node --import=tsx/esm --test --test-timeout=60000 test/*.ts",
        ],
        "ky": [
            "npm run build",
            "./node_modules/.bin/tsc --project tsconfig.test.json",
            "./node_modules/.bin/ava --timeout=2m --concurrency=2",
        ],
        "chi": ["go test ./..."],
    }[repo]
    paths = {
        "tinydb": ["tinydb/*.py"],
        "dotenv": ["src/dotenv/*.py"],
        "pqueue": ["source/*.ts"],
        "ky": ["source/**/*.ts", "source/*.ts"],
        "chi": ["*.go", "middleware/*.go"],
    }[repo]
    if kind == "N":
        paths = ["navigation-answer.json"]
    elif kind == "T":
        paths = ["benchmark_tests/*"]
    else:
        paths += ["benchmark_tests/*"]
    p = (
        prompt
        + "\n\nWork autonomously. Leave the final output on the main task checkout, not only a branch/worktree. "
    )
    if kind == "N":
        p += "Write navigation-answer.json using the schema in .bench/navigation-answer.schema.json; run python /opt/benchmark-public/validate_answer.py navigation-answer.json. Do not edit source code."
    elif kind == "T":
        p += (
            "Only add tests under benchmark_tests/. Do not edit production code, existing tests or test/config files. The supplied feature implementation has its reference tests withheld; unrelated upstream tests remain intact. Use "
            + (
                {
                    "python": "pytest",
                    "typescript": ("AVA" if repo == "ky" else "node:test"),
                    "go": "Go testing",
                }[lang]
            )
            + "."
        )
    else:
        p += "You may edit production source and add tests under benchmark_tests/, but existing tests/configuration and .bench files are immutable."
    task = {
        "id": ident,
        "repo": repo,
        "upstream": "https://github.com/" + upstream + ".git",
        "commit": commit,
        "category": category,
        "language": lang,
        "title": title,
        "prompt": p,
        "allowed_paths": paths,
        "verification": verify,
        "provenance": "fabricated"
        if kind == "B"
        else ("new_feature_tests_withheld" if kind == "T" else category),
        "setup": setup or {},
        "reference": reference or {},
        "mutants": mutants or [],
        "certification": {
            "status": "pending",
            "reason": "Reference/no-op, regression and mutant checks have not run.",
        },
    }
    write_task(ROOT / "suites/v1/tasks" / f"{ident}.yaml", task)
    oracle_dir = ROOT / "evaluator/v1" / ident
    oracle_dir.mkdir(parents=True, exist_ok=True)
    if oracle:
        (
            oracle_dir
            / (
                "check.py"
                if lang == "python"
                else "check.ts"
                if lang == "typescript"
                else "check_test.go"
            )
        ).write_text(oracle)
    TASKS.append(task)


# Narrow fabricated bugs preserve visible upstream tests and invert a specific runtime behavior.
p = "tinydb/table.py"
base = (ROOT / ".cache/repos/tinydb" / p).read_text()
add(
    "tinydb",
    "B",
    "Query cache survives writes",
    "Searching a cached predicate after update/remove returns stale documents. Restore invalidation for every successful mutation, preserving copy-on-search and unaffected tables.",
    edit(
        p,
        "        # Clear the query cache, as the table contents have changed\n        self.clear_cache()",
        "        # Clear the query cache, as the table contents have changed\n        pass",
    ),
    edit(
        p,
        "        # Clear the query cache, as the table contents have changed\n        pass",
        "        # Clear the query cache, as the table contents have changed\n        self.clear_cache()",
    ),
    """from tinydb import TinyDB, Query
from tinydb.storages import MemoryStorage

def test_cached_queries_after_writes():
 db=TinyDB(storage=MemoryStorage); q=Query().v==1
 db.insert({'v':1}); assert len(db.search(q))==1
 db.update({'v':2}); assert db.search(q)==[]
 db.insert({'v':1}); assert len(db.search(q))==1
 db.remove(q); assert db.search(q)==[]
 other=db.table('other'); other.insert({'v':1}); assert len(other.search(q))==1
""",
)
p = "src/dotenv/main.py"
old = """            else:
                env.update(new_values)
                env.update(os.environ)  # type: ignore"""
bad = """            else:
                env.update(os.environ)  # type: ignore
                env.update(new_values)"""
add(
    "dotenv",
    "B",
    "Interpolation override precedence",
    "When override=False, substitutions must prefer existing environment values over earlier file bindings; override=True must prefer earlier file bindings. Preserve empty values, default substitutions and unassigned keys.",
    edit(p, old, bad),
    edit(p, bad, old),
    """from io import StringIO
from dotenv.main import DotEnv

def test_override_and_empty(monkeypatch):
 monkeypatch.setenv('A','outer')
 assert DotEnv(None,StringIO('A=inner\\nB=${A}'),override=False).dict()['B']=='outer'
 assert DotEnv(None,StringIO('A=inner\\nB=${A}'),override=True).dict()['B']=='inner'
 monkeypatch.setenv('A','')
 assert DotEnv(None,StringIO('A=inner\\nB=${A}'),override=False).dict()['B']==''
""",
)
p = "source/utils/merge.ts"
old = "const result = new globalThis.Headers(source1 as RequestInit['headers']);"
bad = "const result = new globalThis.Headers();"
add(
    "ky",
    "B",
    "Inherited headers disappear",
    "Extending a Ky instance loses inherited headers. Preserve inherited headers while supporting request overrides and undefined deletion markers, including case-insensitive names. Do not mutate parent headers.",
    edit(p, old, bad),
    edit(p, bad, old),
    """import assert from 'node:assert/strict';
import {mergeHeaders} from '/task/source/utils/merge.ts';
const h=mergeHeaders({'X-Base':'base','Authorization':'token'},{'x-new':'new'});
assert.equal(h.get('x-base'),'base');assert.equal(h.get('X-New'),'new');
assert.equal(mergeHeaders(h,{authorization:undefined}).get('authorization'),null);
assert.equal(h.get('authorization'),'token');
assert.equal(mergeHeaders(h,{'x-base':'override'}).get('x-base'),'override');
""",
)
p = "context.go"
old = "\tx.URLParams.Keys = x.URLParams.Keys[:0]\n\tx.URLParams.Values = x.URLParams.Values[:0]"
bad = "\t// Regression: accumulated URLParams were not cleared."
add(
    "chi",
    "B",
    "Pooled context leaks URL params",
    "Context.Reset leaves request URL parameters behind. Restore reset semantics so reused contexts have no parameters and other reset state is preserved. Preserve capacity reuse and nested parameter precedence.",
    edit(p, old, bad),
    edit(p, bad, old),
    """package chi
import "testing"
func TestBenchmarkReset(t *testing.T) {
 c:=NewRouteContext(); c.URLParams.Add("id","old"); c.RoutePath="/old"; c.Reset()
 if len(c.URLParams.Keys)!=0 || len(c.URLParams.Values)!=0 || c.URLParam("id")!="" || c.RoutePath!="" { t.Fatal("request context not cleared") }
 c.URLParams.Add("id","new"); if c.URLParam("id")!="new" {t.Fatal("reuse failed")}
}
""",
)

# Cross-file features pin concrete names/signatures in public briefs.
dbflush = '''    def flush(self) -> None:
        """Persist buffered writes without closing the database."""
        self._storage.flush()

'''
storageflush = '''    def flush(self) -> None:
        """Default storage flush: writes are already synchronous."""
        return None

'''
add(
    "tinydb",
    "X",
    "Public storage flush contract",
    "Add TinyDB.flush() -> None and Storage.flush() -> None. Default Storage.flush is a no-op; JSONStorage remains usable. TinyDB delegates to storage so CachingMiddleware.flush persists pending writes without closing. Calling flush repeatedly is safe; flushing a closed caching middleware still raises ValueError.",
    reference=merge(
        edit(
            "tinydb/database.py",
            "    def close(self) -> None:",
            dbflush + "    def close(self) -> None:",
        ),
        edit(
            "tinydb/storages.py",
            "    def close(self) -> None:",
            storageflush + "    def close(self) -> None:",
        ),
    ),
    oracle="""from tinydb import TinyDB
from tinydb.storages import JSONStorage, MemoryStorage, Storage
from tinydb.middlewares import CachingMiddleware

def test_flush(tmp_path):
 assert Storage.flush(MemoryStorage()) is None
 path=tmp_path/'db.json'; db=TinyDB(path,storage=CachingMiddleware(JSONStorage))
 db.insert({'v':1}); assert db.flush() is None
 assert len(TinyDB(path))==1
 db.insert({'v':2}); db.flush();db.flush();assert len(TinyDB(path))==2
 db.close()
""",
)
# Strict loading uses an explicit error type and adds a CLI run option.
base = (ROOT / ".cache/repos/dotenv/src/dotenv/main.py").read_text()
strictbase = """class DotEnvParseError(ValueError):
    def __init__(self, line: int, path=None):
        self.line = line
        self.path = path
        super().__init__(f"Invalid binding at line {line}: {path or '<stream>'}")


"""
changes = [
    {"old": "class DotEnv:\n", "new": strictbase + "class DotEnv:\n"},
    {
        "old": "        override: bool = True,\n    ) -> None:",
        "new": "        override: bool = True,\n        strict: bool = False,\n    ) -> None:",
    },
    {
        "old": "        self.override: bool = override",
        "new": "        self.override: bool = override\n        self.strict = strict",
    },
    {
        "old": "                if mapping.key is not None:\n                    yield mapping.key, mapping.value",
        "new": "                if mapping.error and self.strict:\n                    raise DotEnvParseError(mapping.original.line, self.dotenv_path)\n                if mapping.key is not None:\n                    yield mapping.key, mapping.value",
    },
    {
        "old": '    encoding: Optional[str] = "utf-8",\n) -> bool:',
        "new": '    encoding: Optional[str] = "utf-8",\n    strict: bool = False,\n) -> bool:',
    },
    {
        "old": '    encoding: Optional[str] = "utf-8",\n) -> Dict[str, Optional[str]]:',
        "new": '    encoding: Optional[str] = "utf-8",\n    strict: bool = False,\n) -> Dict[str, Optional[str]]:',
    },
    {
        "old": "        interpolate=interpolate,\n        override=override,\n        encoding=encoding,\n    )",
        "new": "        interpolate=interpolate,\n        override=override,\n        encoding=encoding,\n        strict=strict,\n    )",
    },
    {
        "old": "        override=True,\n        encoding=encoding,\n    ).dict()",
        "new": "        override=True,\n        encoding=encoding,\n        strict=strict,\n    ).dict()",
    },
]
cli = (ROOT / ".cache/repos/dotenv/src/dotenv/cli.py").read_text()
cliedit = [
    {
        "old": "def run(ctx: click.Context, override: bool, commandline: tuple[str, ...]) -> None:",
        "new": '@click.option("--strict", is_flag=True, default=False)\ndef run(ctx: click.Context, override: bool, commandline: tuple[str, ...], strict: bool = False) -> None:',
    },
    {
        "old": 'DotEnv(file, override=override, encoding="utf-8")',
        "new": 'DotEnv(file, override=override, encoding="utf-8", strict=strict)',
    },
]
add(
    "dotenv",
    "X",
    "Strict dotenv loading API and CLI",
    "Add strict: bool=False to DotEnv, load_dotenv and dotenv_values. In strict mode a malformed binding raises public dotenv.DotEnvParseError(ValueError), with .line (1-based int) and .path (input path or None for streams), before setting any environment variable. Lenient defaults remain unchanged. Add dotenv run --strict that uses this API and exits nonzero on invalid input; valid files still run the command.",
    reference={
        "src/dotenv/main.py": changes,
        "src/dotenv/cli.py": cliedit,
        "src/dotenv/__init__.py": [
            {
                "old": "from .main import dotenv_values,",
                "new": "from .main import DotEnvParseError, dotenv_values,",
            }
        ],
    },
    oracle="""from io import StringIO
import pytest
from dotenv import load_dotenv, dotenv_values, DotEnvParseError
from click.testing import CliRunner
from dotenv.cli import cli

def test_strict(monkeypatch,tmp_path):
 monkeypatch.delenv('BENCH_STRICT',raising=False)
 with pytest.raises(DotEnvParseError) as exc:
  load_dotenv(stream=StringIO('BENCH_STRICT=yes\\nBAD="unclosed'),strict=True)
 assert exc.value.line==2 and exc.value.path is None
 import os
 assert 'BENCH_STRICT' not in os.environ
 assert dotenv_values(stream=StringIO('A=ok\\nBAD="unclosed'))=={'A':'ok'}
 assert dotenv_values(stream=StringIO('A=ok'),strict=True)=={'A':'ok'}
 path=tmp_path/'bad.env';path.write_text('BAD="unclosed')
 assert CliRunner().invoke(cli,['-f',str(path),'run','--strict','python','-c','pass']).exit_code!=0
""",
)
# Atomic bulk priority updates; public wrapper updates its own waiting-task metadata too.
queue_method = """\tsetPriorities(updates: ReadonlyArray<{id: string; priority: number}>): void {
\t\tfor (const {id, priority} of updates) {
\t\t\tif (!Number.isFinite(priority)) { throw new TypeError('priority must be finite'); }
\t\t\tif (this.#findIndex(element => element.id === id) === -1) { throw new ReferenceError('unknown queued id'); }
\t\t}
\t\tfor (const {id, priority} of updates) { this.setPriority(id, priority); }
\t}

"""
public_method = """\tsetPriorities(updates: ReadonlyArray<{id: string; priority: number}>): void {
\t\tfor (const {id, priority} of updates) {
\t\t\tif (!Number.isFinite(priority)) { throw new TypeError('priority must be finite'); }
\t\t\tif (![...this.#waitingTasks].some(task => task.id === id)) { throw new ReferenceError('unknown queued id'); }
\t\t}
\t\tif (this.#queue.setPriorities) { this.#queue.setPriorities(updates); }\n\t\telse { for (const {id, priority} of updates) { this.#queue.setPriority(id, priority); } }
\t\tfor (const {id, priority} of updates) {
\t\t\tfor (const task of this.#waitingTasks) { if (task.id === id) { task.priority = priority; } }
\t\t}
\t}

"""
add(
    "pqueue",
    "X",
    "Atomic bulk queue priority updates",
    "Add setPriorities(updates: ReadonlyArray<{id:string;priority:number}>): void to PQueue, the Queue interface and PriorityQueue. Make the Queue capability optional to preserve existing custom queue types; PQueue must fall back to validated single updates when a custom queue lacks the bulk method. Validate the entire batch before any mutation: nonfinite priority -> TypeError; unknown or already-running ID -> ReferenceError. Empty batch is a no-op; duplicate IDs apply in order (last wins). Reuse single-update ordering, keep waiting-task metadata consistent, and preserve other queued tasks.",
    reference=merge(
        edit(
            "source/queue.ts",
            "\tsetPriority: (id: string, priority: number) => void;",
            "\tsetPriorities?: (updates: ReadonlyArray<{id: string; priority: number}>) => void;\n\tsetPriority: (id: string, priority: number) => void;",
        ),
        edit(
            "source/priority-queue.ts",
            "\tsetPriority(id: string, priority: number) {",
            queue_method + "\tsetPriority(id: string, priority: number) {",
        ),
        edit(
            "source/index.ts",
            "\tsetPriority(id: string, priority: number) {",
            public_method + "\tsetPriority(id: string, priority: number) {",
        ),
    ),
    oracle="""import assert from 'node:assert/strict';
import PQueue from '/task/source/index.ts';
import PriorityQueue from '/task/source/priority-queue.ts';
const q=new PQueue({autoStart:false,concurrency:1});const order:string[]=[];
const promises=['a','b','c'].map(id=>q.add(async()=>{order.push(id);return id;},{id,priority:0}));
q.setPriorities([{id:'c',priority:2},{id:'c',priority:3}]);
assert.throws(()=>q.setPriorities([{id:'a',priority:100},{id:'missing',priority:1}]),ReferenceError);
assert.throws(()=>q.setPriorities([{id:'a',priority:NaN}]),TypeError);
q.setPriorities([]);q.start();await Promise.all(promises);assert.deepEqual(order,['c','a','b']);
const p=new PriorityQueue();p.enqueue(async()=>1,{id:'a'});p.setPriorities([{id:'a',priority:5}]);assert.equal(p.size,1);
""",
)
add(
    "ky",
    "X",
    "Safe HTTP error header summaries",
    "Add errorHeaderRedaction?: readonly string[] to Ky options and the option registry. Add readonly requestHeaderSummary: Record<string,string> on HTTPError. It snapshots error.request.headers, redacting Authorization and Cookie by default plus specified names case-insensitively to [REDACTED]. Preserve unlisted headers. Redaction must not mutate the actual request or its headers; error.options retains normalized options. Keep existing error behavior and exports.",
    reference=merge(
        edit(
            "source/types/options.ts",
            "export type KyOptions = {",
            "export type KyOptions = {\n\terrorHeaderRedaction?: readonly string[];",
        ),
        edit(
            "source/types/options.ts",
            "\treadonly retry: NormalizedRetryOptions;",
            "\treadonly errorHeaderRedaction?: readonly string[];\n\treadonly retry: NormalizedRetryOptions;",
        ),
        edit(
            "source/core/constants.ts",
            "\tthrowHttpErrors: true,",
            "\tthrowHttpErrors: true,\n\terrorHeaderRedaction: true,",
        ),
        edit(
            "source/errors/HTTPError.ts",
            "\tdata: T | string | undefined;",
            "\tdata: T | string | undefined;\n\treadonly requestHeaderSummary: Record<string, string>;",
        ),
        edit(
            "source/errors/HTTPError.ts",
            "\t\tthis.options = options;",
            "\t\tthis.options = options;\n\t\tconst hidden = new Set(['authorization', 'cookie', ...(options.errorHeaderRedaction ?? []).map(name => name.toLowerCase())]);\n\t\tthis.requestHeaderSummary = Object.fromEntries([...request.headers].map(([name, value]) => [name, hidden.has(name.toLowerCase()) ? '[REDACTED]' : value]));",
        ),
    ),
    oracle="""import assert from 'node:assert/strict';
import ky, {HTTPError} from '/task/source/index.ts';
try { await ky('https://example.test',{headers:{Authorization:'secret',Cookie:'session', 'X-Key':'private','X-Public':'ok'},errorHeaderRedaction:['X-KEY'],retry:0,fetch:async()=>new Response('bad',{status:400})});assert.fail('expected HTTPError'); }
catch(e) {assert.ok(e instanceof HTTPError);assert.equal(e.requestHeaderSummary.authorization,'[REDACTED]');assert.equal(e.requestHeaderSummary.cookie,'[REDACTED]');assert.equal(e.requestHeaderSummary['x-key'],'[REDACTED]');assert.equal(e.requestHeaderSummary['x-public'],'ok');assert.equal(e.request.headers.get('authorization'),'secret');}
""",
)

# Navigation rubrics use symbols/edges and structured claims, not prose phrase matching.
NAV = {
    "dotenv": (
        "Trace CLI environment construction and override semantics. Explain why unassigned keys are excluded and identify the final environment merge. Claims: override_precedence (environment or file), unassigned (excluded), execution (exec/subprocess).",
        {
            "symbols": [
                {"path": "src/dotenv/cli.py", "name": "run"},
                {"path": "src/dotenv/cli.py", "name": "run_command"},
                {"path": "src/dotenv/main.py", "name": "DotEnv.dict"},
                {"path": "src/dotenv/main.py", "name": "resolve_variables"},
            ],
            "edges": [
                {"from": "run", "to": "DotEnv.dict"},
                {"from": "DotEnv.dict", "to": "resolve_variables"},
                {"from": "run", "to": "run_command"},
            ],
            "claims": {
                "override_precedence": "file when true; environment when false",
                "unassigned": "excluded",
                "execution": "exec/subprocess",
            },
        },
    ),
    "pqueue": (
        "Trace PQueue.add through priority enqueue, execution and completion bookkeeping. Claims: concurrency (pending less than concurrency), completion (finally), idle (pending and queue both zero).",
        {
            "symbols": [
                {"path": "source/index.ts", "name": "PQueue.add"},
                {"path": "source/index.ts", "name": "PQueue.#next"},
                {"path": "source/priority-queue.ts", "name": "PriorityQueue.enqueue"},
                {"path": "source/index.ts", "name": "PQueue.#tryToStartAnother"},
            ],
            "edges": [
                {"from": "PQueue.add", "to": "PriorityQueue.enqueue"},
                {"from": "PQueue.add", "to": "PQueue.#next"},
                {"from": "PQueue.#next", "to": "PQueue.#processQueue"},
            ],
            "claims": {
                "concurrency": "pending less than concurrency",
                "completion": "finally",
                "idle": "pending and queue both zero",
            },
        },
    ),
    "ky": (
        "Trace timeout and cancellation from Ky request construction through retry/delay utilities and exported error types. Claims: timeout_error (TimeoutError), abort_signal (AbortSignal), retry_delay (abortable).",
        {
            "symbols": [
                {"path": "source/core/Ky.ts", "name": "Ky"},
                {"path": "source/core/Ky.ts", "name": "Ky.#fetch"},
                {"path": "source/core/Ky.ts", "name": "Ky.#retryFromError"},
                {"path": "source/utils/timeout.ts", "name": "timeout"},
                {"path": "source/utils/delay.ts", "name": "delay"},
                {"path": "source/errors/TimeoutError.ts", "name": "TimeoutError"},
            ],
            "edges": [
                {"from": "Ky.#fetch", "to": "timeout"},
                {"from": "Ky.#retryFromError", "to": "delay"},
                {"from": "timeout", "to": "TimeoutError"},
            ],
            "claims": {
                "timeout_error": "TimeoutError",
                "abort_signal": "AbortSignal",
                "retry_delay": "abortable",
            },
        },
    ),
    "chi": (
        "Explain nested route parameter lookup, pooled routing-context reuse and request context access. Claims: precedence (last matching parameter), reset (clear parameters), pool (Get and Put around request).",
        {
            "symbols": [
                {"path": "context.go", "name": "Context.URLParam"},
                {"path": "context.go", "name": "Context.Reset"},
                {"path": "context.go", "name": "URLParamFromCtx"},
                {"path": "mux.go", "name": "Mux.ServeHTTP"},
            ],
            "edges": [
                {"from": "URLParamFromCtx", "to": "RouteContext"},
                {"from": "URLParamFromCtx", "to": "Context.URLParam"},
                {"from": "Mux.ServeHTTP", "to": "Context.Reset"},
            ],
            "claims": {
                "precedence": "last matching parameter",
                "reset": "clear parameters",
                "pool": "Get and Put around request",
            },
        },
    ),
}
CHOICES = {
    "dotenv": {
        "override_precedence": [
            "environment always",
            "file always",
            "file when true; environment when false",
        ],
        "unassigned": ["excluded", "empty string", "literal null"],
        "execution": ["exec/subprocess", "in-process eval", "shell source"],
    },
    "pqueue": {
        "concurrency": [
            "pending less than concurrency",
            "queue size less than concurrency",
            "pending equal concurrency",
        ],
        "completion": ["finally", "only fulfillment", "before execution"],
        "idle": [
            "pending and queue both zero",
            "queue empty regardless of pending",
            "pending zero regardless of queue",
        ],
    },
    "ky": {
        "timeout_error": ["TimeoutError", "AbortError", "HTTPError"],
        "abort_signal": ["AbortSignal", "CancellationToken", "none"],
        "retry_delay": ["abortable", "ignores cancellation", "synchronous sleep"],
    },
    "chi": {
        "precedence": [
            "last matching parameter",
            "first matching parameter",
            "all matching values joined",
        ],
        "reset": ["clear parameters", "retain parameters", "drop context object"],
        "pool": [
            "Get and Put around request",
            "one context shared by all requests",
            "new context without reuse",
        ],
    },
}
for repo, (prompt, rubric) in NAV.items():
    ident = repo + "-N"
    answer = {"task_id": ident, **rubric}
    choices = CHOICES[repo]
    prompt = (
        prompt.split("Claims:")[0]
        + "Write the call graph and select each claim value from these public choices: "
        + json.dumps(choices)
    )
    add(
        repo,
        "N",
        "Code path and lifecycle navigation",
        prompt,
        reference={"navigation-answer.json": json.dumps(answer, indent=2) + "\n"},
    )
    write_json(
        ROOT / "evaluator/v1" / ident / "rubric.json",
        {
            "symbols": rubric["symbols"],
            "edges": rubric["edges"],
            "claims": rubric["claims"],
            "authors": ["Codex-assisted fixture author; not independently reviewed"],
            "equivalents": {},
        },
    )

# Mutation-certified test tasks: correct new features with reference tests withheld.
# Unrelated upstream tests remain untouched; this explicit replacement avoids equivalent/already-killed mutants.
helper = """    def flush_if_pending(self, min_writes: int = 1) -> bool:
        if self._closed:
            raise ValueError('I/O operation on closed storage')
        if min_writes < 1:
            raise ValueError('min_writes must be positive')
        if self._cache_modified_count < min_writes:
            return False
        self.flush()
        return True

"""
setup = edit(
    "tinydb/middlewares.py", "    def flush(self):", helper + "    def flush(self):"
)
mut = [
    edit(
        "tinydb/middlewares.py",
        "        self.flush()\n        return True",
        "        pass\n        return True",
    ),
    edit(
        "tinydb/middlewares.py",
        "        if self._cache_modified_count < min_writes:",
        "        if self._cache_modified_count <= min_writes:",
    ),
    edit(
        "tinydb/middlewares.py",
        "        if min_writes < 1:",
        "        if min_writes < 0:",
    ),
    edit(
        "tinydb/middlewares.py",
        "        self.flush()\n        return True",
        "        self.flush()\n        return False",
    ),
    edit(
        "tinydb/middlewares.py",
        "        if self._closed:\n            raise ValueError('I/O operation on closed storage')\n        if min_writes",
        "        if min_writes",
    ),
]
ref = """from tinydb.middlewares import CachingMiddleware
from tinydb.storages import MemoryStorage
import pytest

def test_flush_if_pending():
 s=CachingMiddleware(MemoryStorage)();assert s.flush_if_pending() is False
 s.write({'x':1});assert s.flush_if_pending(2) is False
 assert s.flush_if_pending(1) is True;assert s.storage.read()=={'x':1};assert s.flush_if_pending() is False
 s.write({'x':2});assert s.flush_if_pending() is True
 with pytest.raises(ValueError):s.flush_if_pending(0)
 s.close()
 with pytest.raises(ValueError):s.flush_if_pending()
"""
add(
    "tinydb",
    "T",
    "Test a conditional middleware flush",
    "The supplied CachingMiddleware.flush_if_pending(min_writes=1)->bool flushes only at the threshold, returns whether it flushed, rejects nonpositive thresholds and closed storage, preserves pending data below threshold, and is safe after a flush. Add deterministic regression tests for this supplied feature.",
    setup,
    {"benchmark_tests/test_flush.py": ref},
    mutants=mut,
)
method = """\tasync settleAll<Result>(tasks: ReadonlyArray<() => Promise<Result>>): Promise<Array<PromiseSettledResult<Result>>> {
\t\treturn Promise.allSettled(tasks.map(task => this.add(task))) as Promise<Array<PromiseSettledResult<Result>>>;
\t}

"""
setup = edit(
    "source/index.ts",
    "\tsetPriority(id: string, priority: number) {",
    method + "\tsetPriority(id: string, priority: number) {",
)
mut = [
    edit(
        "source/index.ts",
        "tasks.map(task => this.add(task))",
        "tasks.map(task => task())",
    ),
    edit(
        "source/index.ts",
        "Promise.allSettled(tasks.map(task => this.add(task)))",
        "Promise.all(tasks.map(task => this.add(task)))",
    ),
    edit(
        "source/index.ts",
        "tasks.map(task => this.add(task))",
        "[...tasks].reverse().map(task => this.add(task))",
    ),
    edit(
        "source/index.ts",
        "tasks.map(task => this.add(task))",
        "tasks.map(task => this.add(task).catch(() => undefined))",
    ),
    edit(
        "source/index.ts",
        "tasks.map(task => this.add(task))",
        "tasks.slice(0, -1).map(task => this.add(task))",
    ),
]
ref = """import test from 'node:test';import assert from 'node:assert/strict';import PQueue from '../source/index.ts';
test('settleAll honors scheduling, order, failures and empty arrays',async()=>{
 const q=new PQueue({concurrency:1});let live=0;let peak=0;const reason=new Error('expected');
 const work=(value:number)=>async()=>{live++;peak=Math.max(peak,live);await Promise.resolve();live--;return value;};
 const result=await q.settleAll([work(1),async()=>{throw reason;},work(3)]);
 assert.equal(peak,1);assert.deepEqual(result,[{status:'fulfilled',value:1},{status:'rejected',reason},{status:'fulfilled',value:3}]);
 assert.deepEqual(await q.settleAll([]),[]);assert.equal(q.pending,0);assert.equal(q.size,0);
});
"""
add(
    "pqueue",
    "T",
    "Test settled queue batches",
    "The supplied PQueue.settleAll(tasks) schedules every callback through this queue and returns all settled outcomes in input order, preserving rejection reasons. Empty batches resolve to []; concurrency and completion counters remain correct. Test this supplied feature, including rejection and deterministic concurrency cases.",
    setup,
    {"benchmark_tests/settle.test.ts": ref},
    mutants=mut,
)
helper = """export const withoutHeaders = (input: Request, names: readonly string[]): Request => {
 const headers = new Headers(input.headers);
 for (const name of names) { headers.delete(name); }
 return new Request(input.clone(), {headers});
};
"""
setup = (
    {
        "source/utils/redact-request.ts": helper,
        "source/index.ts": [{"old": "import {Ky}", "new": "import {Ky}"}],
    }
    if False
    else {
        "source/utils/redact-request.ts": helper,
        "source/index.ts": [
            {
                "old": "export {HTTPError}",
                "new": "export {withoutHeaders} from './utils/redact-request.js';\nexport {HTTPError}",
            }
        ],
    }
)
mut = [
    edit(
        "source/utils/redact-request.ts",
        "const headers = new Headers(input.headers);",
        "const headers = input.headers;",
    ),
    edit(
        "source/utils/redact-request.ts",
        "for (const name of names)",
        "for (const name of names.slice(0,1))",
    ),
    edit(
        "source/utils/redact-request.ts", "new Headers(input.headers)", "new Headers()"
    ),
    edit("source/utils/redact-request.ts", "{headers});", "{headers, method: 'PUT'});"),
    edit(
        "source/utils/redact-request.ts",
        "new Request(input.clone(), {headers})",
        "new Request(input.url, {headers, method:input.method})",
    ),
]
ref = """import test from 'ava';import {withoutHeaders} from '../source/index.ts';
test('withoutHeaders preserves source, body, method and unlisted names',async t=>{
 const input=new Request('https://example.test',{method:'POST',body:'payload',headers:{Authorization:'s','X-Key':'k','X-Public':'p'}});
 const output=withoutHeaders(input,['AUTHORIZATION','x-key']);
 t.is(output.headers.get('authorization'),null);t.is(output.headers.get('x-key'),null);t.is(output.headers.get('x-public'),'p');
 t.is(input.headers.get('authorization'),'s');t.is(output.method,'POST');t.is(output.url,input.url);
 t.is(await output.text(),'payload');t.is(await input.text(),'payload');
});
"""
add(
    "ky",
    "T",
    "Test request header removal and cloning",
    "The supplied exported withoutHeaders(input:Request,names:readonly string[]):Request removes named headers case-insensitively, preserves all other headers, URL, method and body, and must not consume or mutate the original request. Add AVA tests for this supplied feature without external network or timing sleeps.",
    setup,
    {"benchmark_tests/request.test.ts": ref},
    mutants=mut,
)
helper = """package middleware

import "net/http"

type ResponseSnapshot struct {
 Status int
 Bytes int
 Headers http.Header
}

func SnapshotResponseWriter(w WrapResponseWriter) ResponseSnapshot {
 return ResponseSnapshot{Status:w.Status(), Bytes:w.BytesWritten(), Headers:w.Header().Clone()}
}
"""
setup = {"middleware/response_snapshot.go": helper}
mut = [
    edit("middleware/response_snapshot.go", "Status:w.Status()", "Status:200"),
    edit("middleware/response_snapshot.go", "Bytes:w.BytesWritten()", "Bytes:0"),
    edit(
        "middleware/response_snapshot.go",
        "Headers:w.Header().Clone()",
        "Headers:w.Header()",
    ),
    edit(
        "middleware/response_snapshot.go",
        "Headers:w.Header().Clone()",
        "Headers:http.Header{}",
    ),
    edit("middleware/response_snapshot.go", "Status:w.Status()", "Status:w.Status()+1"),
]
ref = """package middleware
import("testing";"net/http/httptest")
func TestBenchmarkSnapshot(t *testing.T){
 w:=NewWrapResponseWriter(httptest.NewRecorder(),1);w.Header().Add("X-Test","one");w.Header().Add("X-Test","two");w.WriteHeader(418);w.Write([]byte("abc"))
 s:=SnapshotResponseWriter(w);if s.Status!=418 || s.Bytes!=3 || len(s.Headers.Values("X-Test"))!=2{t.Fatal("snapshot fields")}
 s.Headers["X-Test"][0]="changed";if w.Header().Values("X-Test")[0]!="one"{t.Fatal("snapshot shares header slice")}
 w.Header().Add("X-New","v");if s.Headers.Get("X-New")!=""{t.Fatal("snapshot shares map")}
}
"""
add(
    "chi",
    "T",
    "Test immutable writer snapshots",
    "The supplied middleware.SnapshotResponseWriter(w WrapResponseWriter) returns ResponseSnapshot{Status,Bytes,Headers}, where counters match w and Headers is a deep snapshot. Test counters, multi-value headers, and independence from later map/slice mutation. Add Go tests under benchmark_tests/ using an external package importing github.com/go-chi/chi/v5/middleware; do not change supplied code.",
    setup,
    {
        "benchmark_tests/snapshot_test.go": ref.replace(
            "package middleware", "package benchmark_tests"
        ).replace(
            '"net/http/httptest")',
            '"net/http/httptest"; . "github.com/go-chi/chi/v5/middleware")',
        )
    },
    mutants=mut,
)
write_json(
    ROOT / "suites/v1/repos.lock.json",
    {
        r: {
            "upstream": v[0],
            "commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT / ".cache/repos" / r, text=True
            ).strip(),
            "language": v[1],
        }
        for r, v in REPOS.items()
    },
)
print(
    "Authored",
    len(TASKS),
    "candidate manifests and private references; certification remains required.",
)

# Private Node oracles run as ES modules outside the task package root.
for oracle in (ROOT / "evaluator").rglob("check.ts"):
    (oracle.parent / "package.json").write_text('{"type":"module"}\n')
