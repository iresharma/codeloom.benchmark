import json,pytest
from tinydb import TinyDB,Query
from tinydb.storages import MemoryStorage
def test_roundtrip_and_cache():
  db=TinyDB(storage=MemoryStorage); table=db.table('notes')
  table.insert({'name':'old'}); assert table.search(Query().name=='old')
  db.import_json('{"notes":{"5":{"name":"new"}}}')
  assert table.search(Query().name=='old')==[]
  assert table.get(doc_id=5)=={'name':'new'}
  assert table.insert({'name':'next'})==6
  assert json.loads(db.export_json())['notes']['5']=={'name':'new'}
  before=db.export_json()
  for bad in ['[]','{"notes":[]}','{"notes":{"0":{}}}','{"notes":{"1":1}}','{']:
    with pytest.raises(ValueError): db.import_json(bad)
    assert db.export_json()==before
def test_storage_failure_does_not_clear_cache():
  class Failing(MemoryStorage):
    fail=False
    def write(self,data):
      if self.fail: raise OSError('disk full')
      super().write(data)
  db=TinyDB(storage=Failing); table=db.table('notes');table.insert({'name':'old'})
  assert table.search(Query().name=='old')
  db.storage.fail=True
  with pytest.raises(OSError): db.import_json('{"notes":{"5":{"name":"new"}}}')
  assert table.search(Query().name=='old')
