from tinydb import TinyDB
from tinydb.storages import MemoryStorage


def test_empty_and_sparse_ids_are_sorted_and_read_only():
    db = TinyDB(storage=MemoryStorage)
    table = db.table("smoke")
    assert table.document_ids() == []
    ids = table.insert_multiple([{"v": i} for i in range(3)])
    table.remove(doc_ids=[ids[1]])
    before = [(document.doc_id, dict(document)) for document in table.all()]
    found = table.document_ids()
    assert found == sorted([ids[0], ids[2]])
    assert all(isinstance(value, int) for value in found)
    found.append(999)
    assert [(document.doc_id, dict(document)) for document in table.all()] == before
    assert table.document_ids() == sorted([ids[0], ids[2]])


def test_tables_and_default_table_remain_independent():
    db = TinyDB(storage=MemoryStorage)
    first = db.insert({"default": True})
    other = db.table("other")
    second = other.insert({"other": True})
    other.remove(doc_ids=[second])
    assert db.document_ids() == [first]
    assert other.document_ids() == []
