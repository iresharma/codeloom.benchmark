"""
Hidden oracle check for `realistic-tinydb-validator`.

Independently verifies the per-table document validator feature,
including its interaction with CachingMiddleware. Not part of tinydb's
own test suite.
"""

import pytest

from tinydb import TinyDB
from tinydb.storages import MemoryStorage
from tinydb.middlewares import CachingMiddleware
from tinydb.table import ValidationError


def only_positive(doc):
    return doc.get('n', 0) > 0


def test_validator_blocks_insert_and_leaves_table_unmodified():
    db = TinyDB(storage=MemoryStorage)
    table = db.table('t', validator=only_positive)

    table.insert({'n': 5})
    assert len(table) == 1

    with pytest.raises(ValidationError):
        table.insert({'n': -1})

    # The rejected document must not have been persisted at all
    assert len(table) == 1
    assert table.all() == [{'n': 5}]


def test_validator_blocks_insert_multiple_atomically():
    db = TinyDB(storage=MemoryStorage)
    table = db.table('t', validator=only_positive)

    with pytest.raises(ValidationError):
        table.insert_multiple([{'n': 1}, {'n': 2}, {'n': -1}, {'n': 3}])

    # Even though the first two documents in the batch were valid, the
    # whole batch must be rejected -- nothing partially written.
    assert len(table) == 0


def test_validator_blocks_update_via_fields_dict():
    db = TinyDB(storage=MemoryStorage)
    table = db.table('t', validator=only_positive)
    doc_id = table.insert({'n': 1})

    with pytest.raises(ValidationError):
        table.update({'n': -9}, doc_ids=[doc_id])

    # The document must be byte-for-byte unchanged
    assert table.get(doc_id=doc_id) == {'n': 1}


def test_validator_blocks_update_via_callable_without_corrupting_live_doc():
    db = TinyDB(storage=MemoryStorage)
    table = db.table('t', validator=only_positive)
    doc_id = table.insert({'n': 1, 'tag': 'keep'})

    def bad_mutation(doc):
        doc['n'] = -5
        doc['tag'] = 'corrupted'

    with pytest.raises(ValidationError):
        table.update(bad_mutation, doc_ids=[doc_id])

    # With MemoryStorage, the in-memory dict can be the *same object* the
    # table holds internally. A naive implementation that calls the
    # mutator directly on the live document and only *then* validates
    # would leave the document corrupted even though it raised. Make sure
    # that doesn't happen.
    doc = table.get(doc_id=doc_id)
    assert doc == {'n': 1, 'tag': 'keep'}


def test_validator_exception_is_wrapped_as_validation_error():
    def picky(doc):
        if 'required' not in doc:
            raise KeyError('required')
        return True

    db = TinyDB(storage=MemoryStorage)
    table = db.table('t', validator=picky)

    with pytest.raises(ValidationError):
        table.insert({'other': 1})

    assert len(table) == 0

    # A document that satisfies the validator still goes through fine
    table.insert({'required': True})
    assert len(table) == 1


def test_validator_can_be_set_and_cleared_at_runtime():
    db = TinyDB(storage=MemoryStorage)
    table = db.table('t')

    # No validator yet -- anything goes
    table.insert({'n': -1})
    assert len(table) == 1

    table.set_validator(only_positive)
    with pytest.raises(ValidationError):
        table.insert({'n': -2})
    assert len(table) == 1

    table.set_validator(None)
    table.insert({'n': -3})
    assert len(table) == 2


def test_validator_runs_before_document_enters_caching_middleware_cache():
    """
    The critical requirement: with CachingMiddleware, an invalid document
    must never make it into the write cache, not even transiently. If
    validation only ran at flush time, the invalid document would sit in
    the cache (silently, since nothing forces a flush) and this test's
    assertions about immediate rejection plus post-flush state would both
    fail for an implementation that validates late.
    """
    storage = CachingMiddleware(MemoryStorage)()
    # Make sure writes are *not* auto-flushed by raising the cache
    # threshold well above the number of operations we perform.
    storage.WRITE_CACHE_SIZE = 10_000

    db = TinyDB(storage=storage)
    table = db.table('t', validator=only_positive)

    table.insert({'n': 1})

    with pytest.raises(ValidationError):
        table.insert({'n': -1})

    # Still just one document -- the invalid write must have been
    # rejected before it could be queued in the middleware's cache.
    assert len(table) == 1
    assert table.all() == [{'n': 1}]

    # Flushing (closing) must not suddenly reveal a cached invalid write.
    db.close()

    db2 = TinyDB(storage=CachingMiddleware(MemoryStorage))
    # (separate instance; just confirming close() didn't raise/crash and
    # the original storage wasn't left in a broken state)
    db2.close()
