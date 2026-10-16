import asyncio,httpx
def test_timeout_layers():
  timeout=httpx.Timeout((1,2,3,4))
  assert timeout.as_dict()==dict(connect=1,read=2,write=3,pool=4)
  seen=[]
  def handle(request):
    seen.append(request.extensions['timeout'])
    return httpx.Response(200)
  with httpx.Client(transport=httpx.MockTransport(handle),timeout=timeout) as client:
    client.send(httpx.Request('GET','http://test'))
    client.get('http://test',timeout=None)
    client.send(httpx.Request('GET','http://test',extensions={'timeout':dict(connect=8,read=7,write=6,pool=5)}))
  assert seen==[timeout.as_dict(),dict(connect=None,read=None,write=None,pool=None),dict(connect=8,read=7,write=6,pool=5)]
