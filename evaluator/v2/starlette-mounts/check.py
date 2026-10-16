from starlette.routing import Router,Mount,Route
from starlette.datastructures import URLPath
async def endpoint(request): pass
def test_nested_mount():
  router=Router([Mount('/api',name='api',routes=[Mount('/v1',name='v1',routes=[Route('/users/{username}',endpoint,name='user')])])])
  assert str(router.url_path_for('api:v1:user',username='a b'))=='/api/v1/users/a b'
  assert str(URLPath('/api/v1',protocol='http').make_absolute_url('https://example.org/proxy/'))=='https://example.org/proxy/api/v1'
