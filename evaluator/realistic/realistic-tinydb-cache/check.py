"""
Hidden oracle check for `realistic-tinydb-cache`.

Independently verifies the query-cache stale-read fix and the new
Table.cache_stats(). Not part of tinydb's own test suite.
"""

from tinydb import TinyDB, where
from tinydb.table import Table
from tinydb.storages import MemoryStorage


def test_remove_then_search_same_handle_is_never_stale():
    db = TinyDB(storage=MemoryStorage)
    table = db.table('t')
    table.insert({'a': 1})
    table.insert({'a': 2})
    table.insert({'a': 3})

    q = where('a') == 2
    assert table.search(q) == [{'a': 2}]  # populates the cache

    table.remove(where('a') == 2)

    # Re-running the *same* cached query must reflect the removal.
    assert table.search(q) == []
    assert len(table) == 2


def test_stale_cache_across_two_table_handles_on_same_storage():
    """
    Core regression: two Table objects opened on the *same* underlying
    storage and table name each keep a private query cache. Removing a
    document through one handle must not leave the other handle's cache
    serving the now-deleted document forever.
    """
    storage = MemoryStorage()
    t1 = Table(storage, 'shared')
    t2 = Table(storage, 'shared')

    t1.insert({'a': 1})
    t1.insert({'a': 2})

    q = where('a') == 2

    # Populate t2's cache for q
    assert t2.search(q) == [{'a': 2}]

    # Delete the matching doc through the *other* handle
    removed = t1.remove(where('a') == 2)
    assert removed

    # t2 must see the removal, not a cached stale hit
    assert t2.search(q) == []
    assert len(t2) == 1

    # And a fresh handle opened afterwards must also see correct data
    t3 = Table(storage, 'shared')
    assert t3.search(q) == []
    assert len(t3) == 1


def test_stale_cache_across_handles_also_catches_new_inserts():
    """
    The same class of bug can also hide a document that should now
    *appear*: if a cached empty-result query isn't invalidated when
    another handle inserts a newly-matching document, the first handle
    would wrongly keep reporting "no matches".
    """
    storage = MemoryStorage()
    t1 = Table(storage, 'shared2')
    t2 = Table(storage, 'shared2')

    q = where('a') == 42

    # t1 caches an empty result (nothing matches yet)
    assert t1.search(q) == []

    # t2 inserts a document that *would* match q
    t2.insert({'a': 42})

    # t1's cached empty result must no longer be trusted
    assert t1.search(q) == [{'a': 42}]


def test_cache_stats_has_hits_and_misses_keys():
    db = TinyDB(storage=MemoryStorage)
    table = db.table('t')

    stats = table.cache_stats()
    assert isinstance(stats, dict)
    assert 'hits' in stats
    assert 'misses' in stats
    assert stats['hits'] == 0
    assert stats['misses'] == 0


def test_cache_stats_counts_correctly_across_hits_misses_and_invalidation():
    db = TinyDB(storage=MemoryStorage)
    table = db.table('t')
    table.insert({'a': 1})
    table.insert({'a': 2})

    q_a = where('a') == 2
    q_b = where('a') == 1

    table.search(q_a)  # miss #1
    table.search(q_a)  # hit #1
    table.search(q_a)  # hit #2
    table.search(q_b)  # miss #2

    stats = table.cache_stats()
    assert stats['hits'] == 2
    assert stats['misses'] == 2

    # A write invalidates the cache entry for q_a, so re-running it must
    # count as a fresh miss, not a hit.
    table.remove(where('a') == 2)
    table.search(q_a)  # miss #3 (now returns [])

    stats = table.cache_stats()
    assert stats['misses'] == 3
    assert stats['hits'] == 2


def test_cache_stats_is_per_table():
    db = TinyDB(storage=MemoryStorage)
    t1 = db.table('one')
    t2 = db.table('two')

    t1.insert({'a': 1})
    t2.insert({'a': 1})

    t1.search(where('a') == 1)
    t1.search(where('a') == 1)

    # t2 never searched -- its stats must be untouched by t1's activity
    stats2 = t2.cache_stats()
    assert stats2['hits'] == 0
    assert stats2['misses'] == 0

    stats1 = t1.cache_stats()
    assert stats1['hits'] == 1
    assert stats1['misses'] == 1
