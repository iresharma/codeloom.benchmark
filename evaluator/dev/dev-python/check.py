from tinydb import TinyDB
from tinydb.storages import MemoryStorage


def test_snapshots():
    db = TinyDB(storage=MemoryStorage)
    assert db.snapshot() == {}
    table = db.table("a")
    table.insert({"x": {"n": [1]}})
    snap = db.snapshot()
    snap["a"][0]["x"]["n"].append(2)
    assert table.snapshot()[0]["x"] == {"n": [1]}
    assert snap["a"][0].doc_id == 1
    db.table("b").insert({"y": 3})
    assert sorted(db.snapshot()) == ["a", "b"]
