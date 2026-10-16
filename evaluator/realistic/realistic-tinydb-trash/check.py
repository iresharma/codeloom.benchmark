"""
Hidden oracle check for `realistic-tinydb-trash`.

Independently verifies Table.trash()/restore()/purge_trash() soft-delete
semantics. Not part of tinydb's own test suite.
"""

from tinydb import TinyDB, where
from tinydb.storages import MemoryStorage


def _fresh_table():
    db = TinyDB(storage=MemoryStorage)
    return db, db.table('items')


def test_trash_hides_from_all_query_paths():
    db, table = _fresh_table()

    id1 = table.insert({'name': 'a', 'n': 1})
    id2 = table.insert({'name': 'b', 'n': 2})
    id3 = table.insert({'name': 'c', 'n': 3})

    trashed_ids = table.trash([id2])
    assert trashed_ids == [id2]

    # len() must not count trashed documents
    assert len(table) == 2

    # all() must not include it
    all_names = sorted(d['name'] for d in table.all())
    assert all_names == ['a', 'c']

    # iteration must not include it
    iter_ids = sorted(doc.doc_id for doc in table)
    assert iter_ids == sorted([id1, id3])

    # search() for a condition that *would* match the trashed doc must not
    # return it
    assert table.search(where('n') == 2) == []

    # get()/contains() by id must report it as gone
    assert table.get(doc_id=id2) is None
    assert table.contains(doc_id=id2) is False

    # the non-trashed documents must be fully intact and keep their IDs
    assert table.get(doc_id=id1) == {'name': 'a', 'n': 1}
    assert table.get(doc_id=id3) == {'name': 'c', 'n': 3}

    db.close()


def test_trash_unknown_ids_are_skipped_and_others_proceed():
    db, table = _fresh_table()
    id1 = table.insert({'n': 1})

    # A mix of a real id and a bogus one: only the real one is trashed,
    # and the return value reflects exactly that.
    result = table.trash([id1, 99999])
    assert result == [id1]
    assert len(table) == 0


def test_restore_preserves_doc_id_and_content():
    db, table = _fresh_table()
    id1 = table.insert({'name': 'a'})
    id2 = table.insert({'name': 'b', 'nested': {'x': [1, 2, 3]}})

    table.trash([id1, id2])
    assert len(table) == 0

    restored = table.restore([id2])
    assert restored == [id2]
    assert len(table) == 1

    doc = table.get(doc_id=id2)
    assert doc is not None
    assert doc.doc_id == id2
    assert doc == {'name': 'b', 'nested': {'x': [1, 2, 3]}}

    # id1 is still trashed
    assert table.get(doc_id=id1) is None

    restored_again = table.restore([id1])
    assert restored_again == [id1]
    assert len(table) == 2
    assert table.get(doc_id=id1) == {'name': 'a'}


def test_restore_of_non_trashed_id_is_noop():
    db, table = _fresh_table()
    id1 = table.insert({'n': 1})

    # id1 was never trashed, so restoring it should be a no-op, not an
    # error and not a duplicate.
    assert table.restore([id1]) == []
    assert len(table) == 1


def test_purge_trash_is_permanent():
    db, table = _fresh_table()
    id1 = table.insert({'n': 1})
    id2 = table.insert({'n': 2})

    table.trash([id1, id2])

    table.purge_trash()

    # Nothing left to restore
    assert table.restore([id1, id2]) == []
    assert len(table) == 0

    # A fresh insert must not collide with a purged id's old slot in a
    # confusing way -- it should simply get the next free id.
    new_id = table.insert({'n': 3})
    assert table.get(doc_id=new_id) == {'n': 3}


def test_trash_is_isolated_per_table():
    db = TinyDB(storage=MemoryStorage)
    t1 = db.table('t1')
    t2 = db.table('t2')

    id1 = t1.insert({'n': 1})
    id2 = t2.insert({'n': 1})  # same doc_id value, different table

    t1.trash([id1])

    # t1's document is hidden ...
    assert len(t1) == 0
    # ... but t2's document (even sharing the same numeric id) is
    # completely unaffected.
    assert len(t2) == 1
    assert t2.get(doc_id=id2) == {'n': 1}


def test_trashing_does_not_corrupt_remaining_document_ids():
    db, table = _fresh_table()
    ids = [table.insert({'n': i}) for i in range(5)]

    table.trash([ids[1], ids[3]])

    remaining = sorted(doc.doc_id for doc in table)
    assert remaining == sorted([ids[0], ids[2], ids[4]])

    for doc_id in remaining:
        assert table.get(doc_id=doc_id) == {'n': ids.index(doc_id)}
