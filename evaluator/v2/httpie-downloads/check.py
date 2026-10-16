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
    output=tmp_path/'download.bin';output.write_bytes(b'abc')
    cli('--download','--continue','--output',str(output),url)
    assert observed[-1].get('Range')=='bytes=3-'
    assert output.read_bytes()==b'abcdef'
  finally: server.shutdown();server.server_close();thread.join()
