import contextlib,json,os,subprocess,sys,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
def test_cli(tmp_path):
  observed=[]
  class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_GET(self):
      observed.append(dict(self.headers))
      body=b'abcdef'
      if self.headers.get('Range')=='bytes=3-':
        self.send_response(206); self.send_header('Content-Range','bytes 3-5/6');body=b'def'
      else: self.send_response(200)
      self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
  thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  url='http://127.0.0.1:'+str(server.server_port)
  env={**os.environ,'HTTPIE_CONFIG_DIR':str(tmp_path/'config')}
  def cli(*args):
    result=subprocess.run([sys.executable,'-m','httpie','--ignore-stdin',*args],env=env,capture_output=True,timeout=10)
    assert result.returncode==0,result.stderr
    return result
  try:
    path=tmp_path/'session.json'
    cli('--session='+str(path),url,'X-First:one')
    original=path.read_bytes()
    cli('--session-read-only='+str(path),url,'X-Second:two')
    assert path.read_bytes()==original
    legacy=json.loads(original);legacy['headers']={'X-Legacy':'yes'};legacy['__meta__']['httpie']='3.1.0'
    path.write_text(json.dumps(legacy))
    upgraded=subprocess.run([sys.executable,'-m','httpie.manager','cli','sessions','upgrade','127.0.0.1',str(path)],env=env,capture_output=True,timeout=10)
    assert upgraded.returncode==0,upgraded.stderr
    cli('--session='+str(path),url)
    assert observed[-1].get('X-Legacy')=='yes'
    assert isinstance(json.loads(path.read_text())['headers'],list)
  finally: server.shutdown();server.server_close();thread.join()
