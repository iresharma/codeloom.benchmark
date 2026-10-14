from tinydb import TinyDB, Query
from tinydb.storages import MemoryStorage


def test_cached_queries_after_writes():
    db = TinyDB(storage=MemoryStorage)
    q = Query().v == 1
    db.insert({"v": 1})
    assert len(db.search(q)) == 1
    db.update({"v": 2})
    assert db.search(q) == []
    db.insert({"v": 1})
    assert len(db.search(q)) == 1
    db.remove(q)
    assert db.search(q) == []
    other = db.table("other")
    other.insert({"v": 1})
    assert len(other.search(q)) == 1
