import asyncio
from starlette.background import BackgroundTasks
from starlette.responses import StreamingResponse
def test_stream_disconnect_and_background():
  async def check():
    seen=[]
    background=BackgroundTasks()
    async def done(i): seen.append(i)
    background.add_task(done,1); background.add_task(done,2)
    await background()
    assert seen==[1,2]
    cleaned=[]
    async def body():
      try:
        yield b'hello'
        await asyncio.sleep(3600)
      finally: cleaned.append(True)
    async def receive():
      await asyncio.sleep(.02)
      return {'type':'http.disconnect'}
    async def send(message): pass
    response=StreamingResponse(body())
    await asyncio.wait_for(response({'type':'http','asgi':{'spec_version':'2.0'}},receive,send),.5)
    assert cleaned==[True]
  asyncio.run(check())
