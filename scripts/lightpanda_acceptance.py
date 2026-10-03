"""Real Lightpanda acceptance against the existing Browser Harness/CDP path.

Starts one disposable test process; never edits production bindings or tabs.
No cookies or page content are written to receipts.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.append("/home/ubuntu/browser-use-venv/lib/python3.12/site-packages")
STATE = Path.home() / ".local/state/agent-runtime/lightpanda-acceptance"
VERSION = "1.0.0"

def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent":"LivingRuntime-Lightpanda-Acceptance"})
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.load(r)

def inventory():
    out = {"python":sys.executable, "architecture":platform.machine()}
    for name in ("browser_harness","websockets","playwright"):
        spec = importlib.util.find_spec(name)
        out[name] = spec.origin if spec else None
    runtime = Path.home()/".local/state/agent-runtime/browser/profiles/chatgpt/runtime.json"
    body = json.loads(runtime.read_text())
    out["chrome_endpoint"] = f"http://127.0.0.1:{body['cdp_port']}"
    out["chrome_pid"] = body["chrome_pid"]
    return out

def install():
    STATE.mkdir(parents=True, exist_ok=True)
    name = {"x86_64":"lightpanda-x86_64-linux","aarch64":"lightpanda-aarch64-linux"}[platform.machine()]
    release = fetch_json(f"https://api.github.com/repos/lightpanda-io/browser/releases/tags/{VERSION}")
    asset = next(a for a in release["assets"] if a["name"] == name)
    dest = STATE / f"lightpanda-{VERSION}"
    if not dest.exists():
        req = urllib.request.Request(asset["browser_download_url"],headers={"User-Agent":"LivingRuntime"})
        with urllib.request.urlopen(req, timeout=90) as r, dest.with_suffix(".download").open("wb") as w:
            while chunk := r.read(1024*1024):
                w.write(chunk)
        dest.with_suffix(".download").replace(dest)
        dest.chmod(0o755)
    digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    if asset.get("digest") and asset["digest"] != "sha256:"+digest:
        raise RuntimeError("Release asset digest mismatch")
    result = subprocess.run([str(dest),"version"],capture_output=True,text=True,timeout=10,check=True)
    return dest, {"version":result.stdout.strip(),"sha256":digest,"release_digest_verified":bool(asset.get("digest"))}

class CDP:
    def __init__(self, url):
        from websockets.sync.client import connect
        self.ws = connect(url,open_timeout=15,max_size=16*1024*1024)
        self.seq = 0
    def call(self, method, params=None, session=None, timeout=20):
        self.seq += 1
        row={"id":self.seq,"method":method,"params":params or {}}
        if session: row["sessionId"]=session
        self.ws.send(json.dumps(row))
        end=time.monotonic()+timeout
        while True:
            msg=json.loads(self.ws.recv(timeout=max(.01,end-time.monotonic())))
            if msg.get("id")!=self.seq: continue
            if "error" in msg: raise RuntimeError(method+": "+json.dumps(msg["error"]))
            return msg.get("result",{})
    def attach(self, target):
        return self.call("Target.attachToTarget",{"targetId":target,"flatten":True})["sessionId"]
    def evaluate(self, expression, session):
        r=self.call("Runtime.evaluate",{"expression":expression,"returnByValue":True,"awaitPromise":True},session)
        if r.get("exceptionDetails"): raise RuntimeError("Runtime.evaluate: "+json.dumps(r["exceptionDetails"]))
        return r.get("result",{}).get("value")
    def close(self): self.ws.close()

class Fixture(BaseHTTPRequestHandler):
    def do_GET(self):
        body=b'<html><title>Lightpanda dynamic acceptance</title><body><input id="prompt"><button id="send" onclick="document.querySelector(\'#answer\').textContent=document.querySelector(\'#prompt\').value">Send</button><div id="answer"></div><script>document.body.dataset.loaded="yes";localStorage.setItem("fixture","ok")</script></body></html>'
        self.send_response(200); self.send_header("Content-Type","text/html"); self.end_headers(); self.wfile.write(body)
    def log_message(self,*args): pass

def sample_rss(pid):
    text=Path(f"/proc/{pid}/status").read_text()
    return int(next(x.split()[1] for x in text.splitlines() if x.startswith("VmRSS:")))

def probe():
    from agent_runtime.integrations.browser_harness import BrowserHarnessProvider, BrowserHarnessRuntime
    from agent_runtime.integrations.browser import BrowserPolicy, BROWSER_NAVIGATE
    binary, release = install()
    info=inventory()
    report={"schema_version":"lightpanda-acceptance.v1","recorded_at":time.time(),
            "release":release,"checks":[],"production_changed":False}
    def check(name, fn):
        started=time.monotonic()
        try:
            value=fn()
            report["checks"].append({"name":name,"status":"PASS","elapsed_seconds":round(time.monotonic()-started,3),"result":value})
            print(json.dumps(report["checks"][-1],ensure_ascii=False),flush=True)
            return value
        except Exception as e:
            report["checks"].append({"name":name,"status":"FAIL","elapsed_seconds":round(time.monotonic()-started,3),"error":str(e)[-1800:]})
            print(json.dumps(report["checks"][-1],ensure_ascii=False),flush=True)
            return None
    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0)); port=sock.getsockname()[1]
    temp=Path(tempfile.mkdtemp(prefix="lp-"))
    server=ThreadingHTTPServer(("127.0.0.1",0),Fixture)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    log=temp/"lightpanda.log"
    with log.open("w") as out:
        proc=subprocess.Popen([str(binary),"serve","--host","127.0.0.1","--port",str(port),"--load-resources","iframe","--load-resources","worker","--load-resources","stylesheet"],stdout=out,stderr=out,
             env={**os.environ,"LIGHTPANDA_DISABLE_TELEMETRY":"true"},start_new_session=True)
        c=None
        try:
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                if proc.poll() is not None: raise RuntimeError("Lightpanda exited: "+log.read_text()[-1800:])
                try:
                    with socket.create_connection(("127.0.0.1",port),timeout=.2): break
                except OSError: time.sleep(.1)
            c=CDP(f"ws://127.0.0.1:{port}/")
            check("CDP Browser.getVersion",lambda:c.call("Browser.getVersion"))
            check("CDP Target.getTargets",lambda:{"targets":len(c.call("Target.getTargets")["targetInfos"])})
            target=c.call("Target.createTarget",{"url":"about:blank"})["targetId"]
            session=c.attach(target)
            local=f"http://127.0.0.1:{server.server_port}/"
            def dynamic():
                c.call("Page.navigate",{"url":local},session)
                for _ in range(30):
                    val=c.evaluate('document.body?.dataset.loaded',session)
                    if val=="yes": break
                    time.sleep(.1)
                assert val=="yes",val
                answer=c.evaluate("(()=>{document.querySelector('#prompt').value='LIGHTPANDA_OK';document.querySelector('#send').click();return document.querySelector('#answer').textContent})()",session)
                assert answer=="LIGHTPANDA_OK",answer
                return {"javascript":True,"form_send_and_read":True}
            check("dynamic_form_send_read",dynamic)
            check("multiple_default_task_tabs",lambda:c.call("Target.createTarget",{"url":"about:blank"}))
            harness="/home/ubuntu/browser-use-venv/bin/browser-harness"
            provider=BrowserHarnessProvider(runtime=BrowserHarnessRuntime(executable=harness,engine="lightpanda",cdp_ws=f"ws://127.0.0.1:{port}/",state_dir=temp/"state",runtime_dir=temp/"harness"),policy=BrowserPolicy(timeout_seconds=15,max_sessions=6))
            def harness_probe():
                result=provider.invoke(BROWSER_NAVIGATE,{"session_id":"lp-public","url":"https://lightpanda.io/"})
                title=provider.evaluate("lp-public","document.title")
                assert title, "empty page title"
                return {"navigation_ok":result["ok"],"title":title}
            check("existing_BrowserHarnessProvider",harness_probe)
            check("Lightpanda_RSS",lambda:{"rss_kib":sample_rss(proc.pid)})
            # Read authenticated state only over the existing Chrome CDP endpoint.
            # Cookie values stay in memory and are never printed or persisted.
            chrome_version=fetch_json(info["chrome_endpoint"]+"/json/version")
            chrome=CDP(chrome_version["webSocketDebuggerUrl"])
            try:
                tabs=chrome.call("Target.getTargets")["targetInfos"]
                sites={"chatgpt":"https://chatgpt.com/"}
                from urllib.parse import urlsplit
                jev=next((t["url"] for t in tabs if "jev" in (urlsplit(t.get("url","")).hostname or "")),None)
                if jev: sites["jev"]=jev
                else: sites["x"]="https://x.com/home"
                cookies=chrome.call("Storage.getCookies").get("cookies",[])
                # Import only the actual target origins and ChatGPT's first party cookies.
                domains={urlsplit(u).hostname for u in sites.values()}
                selected=[x for x in cookies if any(d==x["domain"].lstrip(".") or d.endswith("."+x["domain"].lstrip(".")) for d in domains)]
                allowed={"name","value","domain","path","secure","httpOnly","sameSite","expires"}
                imported=[{k:v for k,v in x.items() if k in allowed} for x in selected]
                check("cookie_import",lambda:(c.call("Network.setCookies",{"cookies":imported},session),{"cookie_count":len(imported)})[-1])
                def harness_cookies():
                    code = "cdp('Network.setCookies', cookies=" + repr(imported) + ")\nresult = {'cookie_count':" + str(len(imported)) + "}"
                    return provider._run("lp-public",code + "\nprint('__AGENT_RUNTIME_BROWSER__' + json.dumps(result))",timeout_seconds=20)
                check("shared_Harness_cookie_import",harness_cookies)
                for name,url in sites.items():
                    def site_probe(name=name,url=url):
                        c.call("Page.navigate",{"url":url},session,timeout=30)
                        latest={}
                        for _ in range(20):
                            latest=c.evaluate("""(()=>({url:location.href,title:document.title,body_chars:(document.body?.innerText||'').length,composer:!!document.querySelector('#prompt-textarea,[contenteditable=true][data-placeholder],textarea'),challenge:/just a moment|verify you are human|checking your browser/i.test(document.body?.innerText||''),login:/log in|sign in/i.test((document.body?.innerText||'').slice(0,2000))}))()""",session)
                            if latest.get("composer") or latest.get("challenge"): break
                            time.sleep(.5)
                        if latest.get("challenge"): raise RuntimeError("ANTI_BOT_CHALLENGE: "+json.dumps(latest))
                        if name=="chatgpt" and not latest.get("composer"): raise RuntimeError("CHATGPT_COMPOSER_UNAVAILABLE: "+json.dumps(latest))
                        return latest
                    observed=check(name+"_page_load",site_probe)
                    check(name+"_Lightpanda_RSS",lambda:{"rss_kib":sample_rss(proc.pid)})
                    if name=="chatgpt" and observed and observed.get("composer"):
                        def auth():
                            value=c.evaluate("(async()=>{const r=await fetch('/api/auth/session');const d=await r.json();return {status:r.status,authenticated:!!d.user}})()",session)
                            assert value.get("authenticated"), value
                            return value
                        logged_in=check("chatgpt_session_authenticated",auth)
                        if logged_in:
                            def real_turn():
                                import asyncio
                                from agent_runtime.llm.chatgpt_browser import ChatGPTBrowserProvider
                                provider._save_target_id("lp-chatgpt",provider._load_target_id("lp-public"))
                                provider.invoke(BROWSER_NAVIGATE,{"session_id":"lp-chatgpt","url":"https://chatgpt.com/","reuse_current":True})
                                llm=ChatGPTBrowserProvider(browser=provider,session_id="lp-chatgpt",
                                    response_timeout_seconds=90,process_lock_path=temp/"chat.lock",
                                    conversation_state_path=temp/"chat.json",refresh_after_turns=0)
                                answer=asyncio.run(llm.complete("This is a disposable browser compatibility test. Reply only LIGHTPANDA_REAL_TURN_OK. Do not create or update saved memories.",max_tokens=32))
                                assert answer.strip()=="LIGHTPANDA_REAL_TURN_OK", "Unexpected acceptance response"
                                return {"sent":True,"response_matched":True,"durable_conversation":bool(llm.last_metadata.get("conversation_id")),"single_target_diagnostic":True}
                            check("existing_ChatGPTBrowserProvider_real_turn",real_turn)
                    if name=="x" and observed:
                        def jev_prepare():
                            sys.path.insert(0,"/home/ubuntu/src/content-agent/src")
                            from wechat_traffic_agent.x_jev_transaction import TRANSACTION
                            spec=importlib.util.find_spec("jev_ultrafast")
                            assert spec and spec.origin, "Jev package unavailable"
                            provider._save_target_id("lp-x",provider._load_target_id("lp-public"))
                            provider.invoke(BROWSER_NAVIGATE,{"session_id":"lp-x","url":"https://x.com/home","reuse_current":True})
                            code=TRANSACTION[:TRANSACTION.index("\ntry:\n    profile =")]
                            code += """
profile = wait_value('document.querySelector("[data-testid=AppTabBar_Profile_Link]")?.getAttribute("href")')
assert profile, 'X authenticated identity unavailable'
navigate('https://x.com/compose/post')
editor = act_observed('fill', text='Lightpanda compatibility probe; this draft is never published.')
expression = '(() => {const e=window.__jevFast?.nodes.get(' + str(editor['node']) + ');return e?.innerText || e?.value || "";})()'
value = wait_value(expression)
assert norm(value) == norm('Lightpanda compatibility probe; this draft is never published.'), 'Jev draft fill mismatch: expected_chars=60 actual_chars=' + str(len(norm(value)))
page = browser.observe(screenshot=False)
buttons = [a for a in page.get('actions', []) if a.get('kind')=='click' and str(a.get('label') or '').strip().casefold() in {'post','发帖','发布','tweet'}]
assert len(buttons)==1, 'Post control not observed'
result = {'authenticated':True, 'jev_fill_verified':True, 'post_control_observed':True, 'published':False}
"""
                            return provider.run_jev_session_script("lp-x",code,package_root=Path(spec.origin).parent.parent,timeout_seconds=90)
                        check("existing_Jev_X_draft_prepare",jev_prepare)
                check("production_Chrome_still_alive",lambda:{"rss_kib":sample_rss(info["chrome_pid"])})
            finally: chrome.close()
        finally:
            if c: c.close()
            if proc.poll() is None:
                os.killpg(proc.pid,signal.SIGTERM); proc.wait(timeout=10)
            for path in temp.rglob("*.pid"):
                try: os.kill(int(path.read_text().strip()),signal.SIGTERM)
                except ProcessLookupError: pass
            server.shutdown()
            report["test_browser_stopped"]=proc.poll() is not None
            import re
            report["log_observations"]={"cloudflare_403": "HTTP 403: cloudflare_challenge" in log.read_text()}
            report["replacement_ready"]=all(x["status"]=="PASS" for x in report["checks"]) and any(x["name"]=="existing_ChatGPTBrowserProvider_real_turn" for x in report["checks"])
            dest=STATE/"latest.json"
            dest.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    return report

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("mode",choices=["inventory","probe"])
    args=parser.parse_args()
    result=inventory() if args.mode=="inventory" else probe()
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if args.mode=="inventory" or result.get("replacement_ready") else 1)
