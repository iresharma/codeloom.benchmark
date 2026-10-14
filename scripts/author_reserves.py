"""Activate the four named reserve slots when Ky cannot certify on the pinned runtime."""

from copy import deepcopy
import json
import shutil

from codeloom_benchmark.artifacts import write_json
from codeloom_benchmark.fixtures import write_task
from codeloom_benchmark.manifests import ROOT, tasks

rows = {t["id"]: t for t in tasks()}
archive = ROOT / "suites/excluded/ky"
archive.mkdir(parents=True, exist_ok=True)
records = []
for ident in ["ky-B", "ky-N", "ky-T", "ky-X"]:
    for ext in [".yaml", ".prompt.json"]:
        source = ROOT / "suites/v1/tasks" / (ident + ext)
        if source.exists():
            shutil.move(source, archive / source.name)
    source = ROOT / "evaluator/v1" / ident
    dest = ROOT / "evaluator/excluded" / ident
    if source.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(source, dest)
    certificate = ROOT / ".cache/certification" / ident / "certification.json"
    if certificate.exists():
        records.append(json.loads(certificate.read_text()))
write_json(
    ROOT / "reports/excluded-ky.json",
    {
        "status": "excluded_before_measurement",
        "reason": "Trusted reference suite required additional browsers and failed timing checks under pinned AMD64 emulation. No upstream tests were deleted or weakened.",
        "certification_records": records,
    },
)


def task(base, ident, title, category, prompt, setup, reference):
    row = deepcopy(rows[base])
    row.update(
        id=ident,
        title=title,
        category=category,
        prompt=prompt
        + "\n\nWork autonomously. Leave the final output on the main checkout. Existing tests/configuration and .bench files are immutable.",
        setup=setup,
        reference=reference,
    )
    row.pop("mutants", None)
    row["allowed_paths"] = (
        ["navigation-answer.json"]
        if category == "navigation"
        else ["benchmark_tests/*"]
        if category == "test-generation"
        else rows[base]["allowed_paths"]
    )
    row["provenance"] = (
        "fabricated"
        if category == "bug"
        else "navigation"
        if category == "navigation"
        else "new_feature"
    )
    row["certification"] = {
        "status": "pending",
        "reason": "Activated reserve before freeze; certification required.",
    }
    write_task(ROOT / "suites/v1/tasks" / (ident + ".yaml"), row)
    directory = ROOT / "evaluator/v1" / ident
    directory.mkdir(parents=True, exist_ok=True)
    return row, directory


old = "const index = this.#findIndex(element => element.id === id);"
bad = "const index = this.#findIndex(element => element.id !== id);"
row, d = task(
    "pqueue-X",
    "pqueue-B",
    "Priority updates target the wrong queued task",
    "bug",
    "setPriority(id, priority) updates a different queued task and may accept a missing ID. Restore matching by ID, including queue entries after previous dequeues. Preserve FIFO behavior at equal priority and missing-ID errors.",
    {"source/priority-queue.ts": [{"old": old, "new": bad}]},
    {"source/priority-queue.ts": [{"old": bad, "new": old}]},
)
(
    d / "check.ts"
).write_text("""import assert from 'node:assert/strict';import PriorityQueue from '/task/source/priority-queue.ts';
const q=new PriorityQueue();const a=async()=>1,b=async()=>2,c=async()=>3;
q.enqueue(a,{id:'a',priority:0});q.enqueue(b,{id:'b',priority:0});q.setPriority('a',5);assert.equal(q.dequeue(),a);assert.throws(()=>q.setPriority('missing',2),ReferenceError);
q.enqueue(c,{id:'c',priority:1});q.setPriority('b',3);assert.equal(q.dequeue(),b);assert.equal(q.dequeue(),c);
""")

rubric = {
    "symbols": [
        {"path": "tinydb/table.py", "name": "Table.search"},
        {"path": "tinydb/table.py", "name": "Table._update_table"},
        {"path": "tinydb/middlewares.py", "name": "CachingMiddleware.write"},
        {"path": "tinydb/middlewares.py", "name": "CachingMiddleware.flush"},
        {"path": "tinydb/storages.py", "name": "JSONStorage.write"},
    ],
    "edges": [
        {"from": "Table._update_table", "to": "CachingMiddleware.write"},
        {"from": "Table._update_table", "to": "Table.clear_cache"},
        {"from": "CachingMiddleware.write", "to": "CachingMiddleware.flush"},
        {"from": "CachingMiddleware.flush", "to": "JSONStorage.write"},
    ],
    "claims": {
        "cached_results": "shallow list copy",
        "invalidation": "after storage write",
        "persistence": "on flush",
    },
}
choices = {
    "cached_results": ["same list", "shallow list copy", "deep copy"],
    "invalidation": ["after storage write", "only on close", "never"],
    "persistence": ["every write", "on flush", "only on close"],
}
answer = {"task_id": "tinydb-N", **rubric}
row, d = task(
    "tinydb-X",
    "tinydb-N",
    "Trace query-cache invalidation and buffered persistence",
    "navigation",
    "For TinyDB(storage=CachingMiddleware(JSONStorage)), trace writes from Table through middleware into JSON persistence. Explain when the query cache is invalidated and whether cached search results protect list membership or nested values. Write navigation-answer.json using .bench/navigation-answer.schema.json and run python .bench/validate_answer.py. Choose each claim value from "
    + json.dumps(choices),
    {},
    {"navigation-answer.json": json.dumps(answer, indent=2) + "\n"},
)
write_json(
    d / "rubric.json",
    {
        **rubric,
        "authors": ["Codex-assisted fixture author; not independently reviewed"],
        "equivalents": {},
    },
)

helper = """def selected_values(values, names):
    result = {}
    for name in names:
        value = values[name]
        if value is not None:
            result[name] = value
    return result


"""
setup = {
    "src/dotenv/main.py": [
        {"old": "def resolve_variables(", "new": helper + "def resolve_variables("}
    ]
}
ref = """import pytest
from dotenv.main import selected_values

def test_selection_and_copy():
    values={'a':'one','blank':'','nil':None,'other':'two'}
    selected=selected_values(values,iter(['a','blank','nil','a']))
    assert selected=={'a':'one','blank':''}
    selected['a']='changed'
    assert values=={'a':'one','blank':'','nil':None,'other':'two'}
    assert selected_values(values,[])=={}

def test_missing_name():
    with pytest.raises(KeyError): selected_values({'a':'one'},['a','missing'])
"""
row, d = task(
    "dotenv-X",
    "dotenv-T",
    "Test selection of assigned dotenv values",
    "test-generation",
    "The supplied dotenv.main.selected_values(values:Mapping[str,Optional[str]], names:Iterable[str])->dict[str,str] selects requested names, omits None values, preserves empty strings, supports repeated names/generators, returns a detached mapping and raises KeyError for any missing name. Add deterministic pytest tests under benchmark_tests/ for this correct new helper; its reference tests are withheld.",
    setup,
    {"benchmark_tests/test_selection.py": ref},
)
row["mutants"] = [
    {"src/dotenv/main.py": [{"old": helper, "new": helper.replace(a, b)}]}
    for a, b in [
        ("if value is not None:", "if True:"),
        ("if value is not None:", "if value:"),
        ("value = values[name]", "value = values.get(name)"),
        ("result[name] = value", "result[name] = name"),
        ("return result", "return values"),
    ]
]
write_task(ROOT / "suites/v1/tasks/dotenv-T.yaml", row)

source = (ROOT / ".cache/repos/chi/middleware/content_type.go").read_text()
a = source.index("func SetHeader(")
b = source.index("\n}\n", a) + 3
old = source[a:b]
new = """func SetHeader(key, value string) func(http.Handler) http.Handler {
 return SetHeaders(http.Header{key: []string{value}})
}
"""
feature = """package middleware

import "net/http"

// SetHeaders snapshots configuration and replaces selected response header values.
func SetHeaders(headers http.Header) func(http.Handler) http.Handler {
 snapshot := headers.Clone()
 return func(next http.Handler) http.Handler {
  return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
   for name, values := range snapshot {
    w.Header().Del(name)
    for _, value := range values { w.Header().Add(name, value) }
   }
   next.ServeHTTP(w, r)
  })
 }
}
"""
row, d = task(
    "chi-B",
    "chi-X",
    "Immutable bulk response-header middleware",
    "cross-file",
    "Add middleware.SetHeaders(headers http.Header) func(http.Handler) http.Handler. Snapshot the configuration at factory creation (map and slices), replace selected response headers with all supplied values before calling the next handler, canonicalize names using net/http semantics, preserve unlisted headers, and copy values per response so handler mutation cannot alter later requests. Nil/empty input is a no-op. Inputs have no duplicate names under case folding. Refactor existing SetHeader(key,value) to delegate to SetHeaders while preserving its behavior. Implement in a new middleware file and update the existing SetHeader definition.",
    {},
    {
        "middleware/bulk_headers.go": feature,
        "middleware/content_type.go": [{"old": old, "new": new}],
    },
)
(d / "check_test.go").write_text("""package chi_test
import("testing";"net/http";"net/http/httptest";"github.com/go-chi/chi/v5/middleware")
func TestBenchmarkReset(t *testing.T){
 config:=http.Header{"x-test": []string{"one","two"}}
 wrap:=middleware.SetHeaders(config);config["x-test"][0]="bad";config.Set("X-New","bad")
 for i:=0;i<2;i++{
  w:=httptest.NewRecorder();w.Header().Set("X-Other","keep");w.Header().Set("X-Test","old")
  wrap(http.HandlerFunc(func(w http.ResponseWriter,r *http.Request){
   v:=w.Header().Values("X-Test");if len(v)!=2||v[0]!="one"||v[1]!="two"{t.Fatal("snapshot or header replacement")}
   if w.Header().Get("X-Other")!="keep"||w.Header().Get("X-New")!=""{t.Fatal("unlisted headers")}
   w.Header()["X-Test"][0]="changed"
  })).ServeHTTP(w,httptest.NewRequest("GET","/",nil))
 }
 middleware.SetHeaders(nil)(http.HandlerFunc(func(http.ResponseWriter,*http.Request){})).ServeHTTP(httptest.NewRecorder(),httptest.NewRequest("GET","/",nil))
}
""")
print(
    "Activated four reserves: 16 tasks, four categories, four repositories; Ky exclusions retained."
)

# Private Node oracles run as ES modules outside the task package root.
for oracle in (ROOT / "evaluator").rglob("check.ts"):
    (oracle.parent / "package.json").write_text('{"type":"module"}\n')
