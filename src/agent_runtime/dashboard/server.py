"""Read-only local HTTP server for the Agent dashboard."""
from __future__ import annotations

import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .digest import build_digest

STATIC_DIR = Path(__file__).parent / "static"


class _Handler(SimpleHTTPRequestHandler):
    server_state_dir: Path

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def do_GET(self) -> None:  # noqa: N802 — http.server API
        parsed = urlsplit(self.path)
        if parsed.path == "/api/digest":
            query = parse_qs(parsed.query)
            since = int(query.get("since", ["0"])[0] or "0")
            payload = build_digest(self.server_state_dir, since=since)
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def log_message(self, format, *args):  # noqa: A002 — quiet by default
        pass


class DashboardServer:
    def __init__(self, state_dir: Path, port: int = 8765) -> None:
        self.state_dir = Path(state_dir).resolve()
        handler = type(
            "BoundHandler", (_Handler,), {"server_state_dir": self.state_dir}
        )
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
        self.port = self.httpd.server_address[1]
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
