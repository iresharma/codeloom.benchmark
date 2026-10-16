import asyncio,httpx
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.middleware.gzip import GZipMiddleware
def test_concurrent_responses():
  async def home(request):
    response=PlainTextResponse('x'*1000,headers={'x-request':request.query_params['id']})
    response.set_cookie('one','1'); response.set_cookie('two','2')
    return response
  async def check():
    app=GZipMiddleware(Starlette(routes=[Route('/',home)]),minimum_size=10)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://test') as client:
      responses=await asyncio.gather(*(client.get('/?id='+str(i),headers={'accept-encoding':'gzip' if i%2 else 'identity'}) for i in range(12)))
    for i,response in enumerate(responses):
      assert response.headers['x-request']==str(i)
      assert len(response.headers.get_list('set-cookie'))==2
      assert response.text=='x'*1000
  asyncio.run(check())
