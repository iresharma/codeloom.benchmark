from tinydb import TinyDB
from tinydb.storages import JSONStorage, MemoryStorage, Storage
from tinydb.middlewares import CachingMiddleware


def test_flush(tmp_path):
    assert Storage.flush(MemoryStorage()) is None
    path = tmp_path / "db.json"
    db = TinyDB(path, storage=CachingMiddleware(JSONStorage))
    db.insert({"v": 1})
    assert db.flush() is None
    assert len(TinyDB(path)) == 1
    db.insert({"v": 2})
    db.flush()
    db.flush()
    assert len(TinyDB(path)) == 2
    db.close()
