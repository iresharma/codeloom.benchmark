"""Two unmeasured development features of similar scope to the measured cross-file tasks."""

from copy import deepcopy
from codeloom_benchmark.fixtures import write_task
from codeloom_benchmark.manifests import ROOT, tasks


def edit(path, old, new):
    return {path: [{"old": old, "new": new}]}


base = deepcopy(next(t for t in tasks() if t["id"] == "tinydb-X"))
base.update(
    id="dev-python",
    title="Independent database and table snapshots",
    prompt="Add Table.snapshot()->list[Document] and TinyDB.snapshot()->dict[str,list[Document]]. Deep-copy document values and preserve IDs, include all table names, return empty structures for empty storage, and leave database contents untouched when snapshots are mutated. Add public methods in the appropriate two classes, preserve existing tests, and leave the main checkout ready for grading.",
    setup={},
    reference={
        "tinydb/table.py": [
            {
                "old": "    def all(self)",
                "new": "    def snapshot(self):\n        from copy import deepcopy\n        return deepcopy(self.all())\n\n    def all(self)",
            }
        ],
        "tinydb/database.py": [
            {
                "old": "    def tables(self)",
                "new": "    def snapshot(self):\n        return {name: self.table(name).snapshot() for name in sorted(self.tables())}\n\n    def tables(self)",
            }
        ],
    },
)
write_task(ROOT / "suites/dev/tasks/dev-python.yaml", base)
d = ROOT / "evaluator/dev/dev-python"
d.mkdir(parents=True, exist_ok=True)
(d / "check.py").write_text("""from tinydb import TinyDB
from tinydb.storages import MemoryStorage

def test_snapshots():
 db=TinyDB(storage=MemoryStorage)
 assert db.snapshot()=={}
 table=db.table('a');table.insert({'x':{'n':[1]}})
 snap=db.snapshot();snap['a'][0]['x']['n'].append(2)
 assert table.snapshot()[0]['x']=={'n':[1]}
 assert snap['a'][0].doc_id==1
 db.table('b').insert({'y':3})
 assert sorted(db.snapshot())==['a','b']
""")
base = deepcopy(next(t for t in tasks() if t["id"] == "pqueue-X"))
base.update(
    id="dev-typescript",
    title="Detached queued-ID snapshots",
    prompt="Add Queue.pendingIds():readonly string[], PriorityQueue.pendingIds() and PQueue.pendingIds():readonly string[]. Return IDs for queued tasks in execution order, excluding started/removed tasks. Return a detached snapshot that cannot change the queue. Reordering/clearing/dequeuing must be reflected in future snapshots. Preserve existing test behavior and leave the main checkout ready for grading. Make the Queue capability optional for existing custom queues; PQueue.pendingIds throws TypeError when a custom queue does not support it.",
    setup={},
    reference={
        "source/queue.ts": [
            {
                "old": "\tsize: number;",
                "new": "\tpendingIds?: () => readonly string[];\n\tsize: number;",
            }
        ],
        "source/priority-queue.ts": [
            {
                "old": "\tenqueue(run: RunFunction,",
                "new": "\tpendingIds(): readonly string[] {\n\t\treturn this.#queue.slice(this.#head).filter(item => item?.id !== undefined).map(item => item!.id!);\n\t}\n\n\tenqueue(run: RunFunction,",
            }
        ],
        "source/index.ts": [
            {
                "old": "\tsetPriority(id: string, priority: number) {",
                "new": "\tpendingIds(): readonly string[] { if (!this.#queue.pendingIds) { throw new TypeError('queue does not support pending IDs'); } return [...this.#queue.pendingIds()]; }\n\n\tsetPriority(id: string, priority: number) {",
            }
        ],
    },
)
write_task(ROOT / "suites/dev/tasks/dev-typescript.yaml", base)
d = ROOT / "evaluator/dev/dev-typescript"
d.mkdir(parents=True, exist_ok=True)
(
    d / "check.ts"
).write_text("""import assert from 'node:assert/strict';import PQueue from '/task/source/index.ts';
const q=new PQueue({autoStart:false,concurrency:1});
const promises=[q.add(async()=>1,{id:'a',priority:1}),q.add(async()=>2,{id:'b',priority:2})];
assert.deepEqual(q.pendingIds(),['b','a']);const ids=q.pendingIds() as string[];ids.reverse();assert.deepEqual(q.pendingIds(),['b','a']);
q.setPriority('a',3);assert.deepEqual(q.pendingIds(),['a','b']);q.start();await Promise.all(promises);assert.deepEqual(q.pendingIds(),[]);
""")
for t in [base]:
    t["certification"] = {"status": "pending"}
print("Authored development tasks; they are excluded from measured outcomes.")

# Private Node oracles run as ES modules outside the task package root.
for oracle in (ROOT / "evaluator").rglob("check.ts"):
    (oracle.parent / "package.json").write_text('{"type":"module"}\n')
