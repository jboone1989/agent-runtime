"""Read the live pending ChatGPT transaction without changing the page or journal."""
import json, hashlib, re, sys, urllib.request
from pathlib import Path
from websockets.sync.client import connect
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from agent_runtime.llm.chatgpt_browser import _CHATGPT_STATE_EXPRESSION
root=Path.home()/".local/state/agent-runtime/browser-harness"
journal=json.loads((root/"chatgpt-profile-chatgpt-chatgpt-cognition-conversation.json").read_text())
binding=json.loads((root/"chatgpt-cognition.json").read_text())
tabs=json.load(urllib.request.urlopen(binding["browser_binding"]+"/json/list",timeout=5))
tab=next(t for t in tabs if t["id"]==binding["target_id"])
with connect(tab["webSocketDebuggerUrl"],open_timeout=5,close_timeout=1) as ws:
    ws.send(json.dumps({"id":1,"method":"Runtime.evaluate","params":{"expression":_CHATGPT_STATE_EXPRESSION,"returnByValue":True,"timeout":5000}}))
    while True:
        response=json.loads(ws.recv(timeout=10))
        if response.get("id")==1:break
state=response["result"]["result"]["value"]
pending=journal.get("pending") or {}
outbound=pending.get("outbound","")
last_user=state.pop("last_user_text","")
composer=state.pop("composer_text","")
last_assistant=state.pop("last_assistant_text","")
marker=re.search(r"Runtime request fingerprint: ([0-9a-f]{64})",outbound)
state.update({"composer_chars":len(composer),"response_chars":len(last_assistant),"last_user_chars":len(last_user),
"composer_matches": " ".join(composer.split())==" ".join(outbound.split()),
"last_user_matches": " ".join(last_user.split())==" ".join(outbound.split()),
"fingerprint_matches": bool(marker and marker.group(0) in last_user)})
print(json.dumps({"state":state,"pending":{k:v for k,v in pending.items() if k!="outbound"}},ensure_ascii=False))
