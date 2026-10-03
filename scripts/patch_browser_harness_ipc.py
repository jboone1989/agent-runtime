"""Patch the installed Browser Harness IPC frame budget and verify real framing."""
from pathlib import Path
import argparse,asyncio,hashlib,json,os,uuid
import browser_harness._ipc as ipc
def patch():
 p=Path(ipc.__file__)
 text=p.read_text()
 old_unix="asyncio.start_unix_server(handler, path=path)"
 old_tcp='asyncio.start_server(handler, "127.0.0.1", 0)'
 if old_unix in text and old_tcp in text:
  text=text.replace(old_unix,"asyncio.start_unix_server(handler, path=path, limit=2 * 1024 * 1024)")
  text=text.replace(old_tcp,'asyncio.start_server(handler, "127.0.0.1", 0, limit=2 * 1024 * 1024)')
  backup=p.with_suffix(".py.pre-ferro-frame-budget")
  if not backup.exists():backup.write_bytes(p.read_bytes())
  compile(text,str(p),"exec")
  tmp=p.with_suffix(".frame.tmp");tmp.write_text(text);os.chmod(tmp,p.stat().st_mode&0o777);os.replace(tmp,p)
 elif "limit=2 * 1024 * 1024" not in text:
  raise RuntimeError("Unrecognized Browser Harness IPC source; review before patching")
 print(json.dumps({"patched_dependency":str(p),"frame_limit_bytes":2*1024*1024}))
async def verify():
 import importlib
 importlib.reload(ipc)
 name="frame-acceptance-"+uuid.uuid4().hex[:12]
 value="中文请求验证"*12000
 received=[]
 async def handler(reader,writer):
  row=json.loads(await reader.readline())
  received.append(row["value"])
  writer.write((json.dumps({"chars":len(row["value"]),"sha256":hashlib.sha256(row["value"].encode()).hexdigest()})+"\n").encode())
  await writer.drain();writer.close();await writer.wait_closed()
 server=asyncio.create_task(ipc.serve(name,handler))
 try:
  for _ in range(100):
   if Path(ipc.sock_addr(name)).exists():break
   await asyncio.sleep(.01)
  def request():
   connection,token=ipc.connect(name,timeout=5)
   try:return ipc.request(connection,token,{"value":value})
   finally:connection.close()
  reply=await asyncio.to_thread(request)
  assert received==[value] and reply["chars"]==len(value)
  print(json.dumps({"unicode_chars":len(value),"encoded_frame_bytes":len((json.dumps({"value":value})+"\n").encode()),"verified":True}))
 finally:
  server.cancel()
  try:await server
  except asyncio.CancelledError:pass
  ipc.cleanup_endpoint(name)
if __name__=="__main__":
 patch()
 asyncio.run(verify())
