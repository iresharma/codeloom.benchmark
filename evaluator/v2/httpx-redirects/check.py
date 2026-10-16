import httpx
def test_origin_boundaries():
  for target,keep in [('https://other.test/a',False),('https://a.test/a',True),('https://a.test:444/a',False)]:
    seen=[]
    def handle(request):
      seen.append(request)
      return httpx.Response(302,headers={'location':target}) if len(seen)==1 else httpx.Response(200)
    with httpx.Client(transport=httpx.MockTransport(handle),follow_redirects=True) as client:
      client.get('http://a.test/a',headers={'Authorization':'Bearer abc','Cookie':'session=secret'})
    assert ('authorization' in seen[-1].headers)==keep
    assert 'cookie' not in seen[-1].headers
