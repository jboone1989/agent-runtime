"""Private localhost chat UI backed by the persistent Runtime mailbox."""
from __future__ import annotations

import argparse
import json
import secrets
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .mailbox import RuntimeMailbox
from ..service import read_runtime_status


class AgentChatServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state_dir: Path) -> None:
        self.state_dir = Path(state_dir)
        self.mailbox = RuntimeMailbox(self.state_dir)
        self.access_token = _access_token(self.mailbox.root / "access_token")
        super().__init__(address, AgentChatHandler)


class AgentChatHandler(BaseHTTPRequestHandler):
    server: AgentChatServer

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            token = parse_qs(parsed.query).get("token", [""])[0]
            if not secrets.compare_digest(token, self.server.access_token):
                self._json(HTTPStatus.FORBIDDEN, {"error": "invalid local access token"})
                return
            body = _page(self.server.access_token).encode("utf-8")
            self._send(HTTPStatus.OK, body, "text/html; charset=utf-8")
            return
        if not self._authorized():
            return
        if parsed.path == "/api/status":
            self._json(
                HTTPStatus.OK,
                {
                    "runtime": read_runtime_status(self.server.state_dir),
                    "responses": [
                        item.__dict__
                        for item in self.server.mailbox.recent_responses(limit=100)
                    ],
                    "notifications": [
                        item.__dict__
                        for item in self.server.mailbox.recent_notifications(limit=50)
                    ],
                    "pending": self.server.mailbox.pending_count(),
                },
            )
            return
        if parsed.path.startswith("/api/messages/"):
            request_id = parsed.path.rsplit("/", 1)[-1]
            response = self.server.mailbox.response(request_id)
            self._json(
                HTTPStatus.OK if response is not None else HTTPStatus.NOT_FOUND,
                {"response": None if response is None else response.__dict__},
            )
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if not self._authorized():
            return
        if parsed.path != "/api/messages":
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("content-length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 25_000:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid request size"})
            return
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("body must be an object")
            request = self.server.mailbox.enqueue(
                str(value.get("message") or ""),
                partner_id=str(value.get("partner_id") or "operator"),
                challenge=bool(value.get("challenge", False)),
            )
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._json(HTTPStatus.ACCEPTED, {"request": request.__dict__})

    def _authorized(self) -> bool:
        supplied = self.headers.get("x-agent-chat-token", "")
        if secrets.compare_digest(supplied, self.server.access_token):
            return True
        self._json(HTTPStatus.FORBIDDEN, {"error": "invalid local access token"})
        return False

    def _json(self, status: HTTPStatus, value: dict[str, Any]) -> None:
        self._send(
            status,
            json.dumps(value, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(int(status))
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        self.send_header("x-content-type-options", "nosniff")
        self.send_header("x-frame-options", "DENY")
        self.send_header(
            "content-security-policy",
            "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'",
        )
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        _ = (format, args)


def build_server(state_dir: Path, *, host: str = "127.0.0.1", port: int = 8765) -> AgentChatServer:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("chat server may bind only to localhost")
    if not 0 <= port <= 65535:
        raise ValueError("port must be between 0 and 65535")
    return AgentChatServer((host, port), Path(state_dir))


def main() -> int:
    parser = argparse.ArgumentParser(description="Private localhost Agent chat")
    parser.add_argument("--state-dir", type=Path, default=Path("state"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = build_server(args.state_dir.resolve(), host=args.host, port=args.port)
    host, port = server.server_address[:2]
    url = f"http://{host}:{port}/?token={server.access_token}"
    print(json.dumps({"status": "RUNNING", "url": url}, ensure_ascii=False), flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _access_token(path: Path) -> str:
    try:
        token = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        token = ""
    if len(token) >= 24:
        return token
    token = secrets.token_urlsafe(32)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(token, encoding="utf-8")
    temporary.replace(path)
    return token


def _page(token: str) -> str:
    config = json.dumps({"token": token}, ensure_ascii=False)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agent 私人通信</title>
<style>
:root{{--bg:#f4f1ea;--panel:#fffdf8;--ink:#22201d;--muted:#766f65;--accent:#315c4c;--line:#ded8ce;--agent:#e3eee8;--user:#ece7dc}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui,"Microsoft YaHei",sans-serif}}
main{{max-width:880px;margin:0 auto;padding:24px 16px 160px}}header{{position:sticky;top:0;background:rgba(244,241,234,.94);backdrop-filter:blur(8px);padding:14px 0;border-bottom:1px solid var(--line);z-index:2}}
h1{{font-size:21px;margin:0}}#status{{color:var(--muted);font-size:13px;margin-top:4px}}#feed{{display:flex;flex-direction:column;gap:14px;padding:22px 0}}
.card{{max-width:82%;padding:12px 14px;border-radius:14px;white-space:pre-wrap;box-shadow:0 1px 1px #0000000d}}.user{{align-self:flex-end;background:var(--user)}}.agent{{align-self:flex-start;background:var(--agent)}}.note{{align-self:center;max-width:92%;background:#fff3cf;border:1px solid #ead58d}}
.meta{{display:block;color:var(--muted);font-size:11px;margin-bottom:5px}}form{{position:fixed;left:0;right:0;bottom:0;background:var(--panel);border-top:1px solid var(--line);padding:12px 16px}}.composer{{max-width:880px;margin:auto;display:grid;grid-template-columns:1fr auto;gap:10px}}
textarea{{resize:none;min-height:68px;max-height:180px;padding:11px;border:1px solid var(--line);border-radius:10px;font:inherit;background:white}}button{{border:0;border-radius:10px;padding:0 22px;background:var(--accent);color:white;font-weight:650;cursor:pointer}}button:disabled{{opacity:.5}}
</style></head><body><main><header><h1>与持续运行的 Agent 对话</h1><div id="status">正在连接本地运行状态……</div></header><section id="feed"></section></main>
<form id="form"><div class="composer"><textarea id="message" placeholder="直接对它说话。Enter 发送，Shift+Enter 换行。" maxlength="20000"></textarea><button id="send">发送</button></div></form>
<script>
const CONFIG={config}; const HEADERS={{'x-agent-chat-token':CONFIG.token}}; const feed=document.querySelector('#feed'); const statusEl=document.querySelector('#status'); const message=document.querySelector('#message'); const send=document.querySelector('#send');
function card(kind,text,meta){{const el=document.createElement('article');el.className='card '+kind;const m=document.createElement('span');m.className='meta';m.textContent=meta;const body=document.createElement('div');body.textContent=text;el.append(m,body);return el}}
async function refresh(){{try{{const r=await fetch('/api/status',{{headers:HEADERS,cache:'no-store'}});const data=await r.json();const s=data.runtime||{{}};statusEl.textContent=`${{s.agent_id||'Agent'}} · ${{s.status||'未知'}} · 周期 ${{s.current_cycle??'?'}} · 待处理 ${{data.pending??0}}`;const rows=[];(data.responses||[]).forEach(x=>{{rows.push([x.created_at,'user',x.message,`你 · ${{new Date(x.created_at).toLocaleString()}}`]);rows.push([x.responded_at,'agent',x.response||('错误：'+x.error),`${{x.agent_id}} · 周期 ${{x.cycle}}`])}});(data.notifications||[]).forEach(x=>rows.push([x.created_at,'note',x.message,`${{x.title}} · 周期 ${{x.cycle}}`]));rows.sort((a,b)=>a[0].localeCompare(b[0]));feed.replaceChildren(...rows.map(x=>card(x[1],x[2],x[3])));window.scrollTo(0,document.body.scrollHeight)}}catch(e){{statusEl.textContent='本地连接失败：'+e.message}}}}
document.querySelector('#form').addEventListener('submit',async e=>{{e.preventDefault();const text=message.value.trim();if(!text)return;send.disabled=true;try{{const r=await fetch('/api/messages',{{method:'POST',headers:{{...HEADERS,'content-type':'application/json'}},body:JSON.stringify({{message:text,partner_id:'operator'}})}});if(!r.ok)throw new Error((await r.json()).error||r.statusText);message.value='';statusEl.textContent='已送达收件箱，等待 Agent 回复……';await refresh()}}catch(err){{statusEl.textContent='发送失败：'+err.message}}finally{{send.disabled=false;message.focus()}}}});
message.addEventListener('keydown',e=>{{if(e.key==='Enter'&&!e.shiftKey){{e.preventDefault();document.querySelector('#form').requestSubmit()}}}});refresh();setInterval(refresh,2000);
</script></body></html>"""


if __name__ == "__main__":
    raise SystemExit(main())
