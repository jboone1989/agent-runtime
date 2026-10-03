"""Real Chrome acceptance on a disposable browser with simulated accounts."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.request
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from agent_runtime.integrations.browser_harness import BrowserHarnessProvider, BrowserHarnessRuntime
from agent_runtime.integrations.browser import BrowserPolicy, BROWSER_NAVIGATE


class Site(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'<html><title>Account acceptance</title><body>Simulated login</body></html>'
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def main():
    chrome = sys.argv[1]
    harness = sys.argv[2]
    root = Path(tempfile.mkdtemp(prefix='shared-browser-acceptance-'))
    server = ThreadingHTTPServer(('127.0.0.1', 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = free_port()
    endpoint = f'http://127.0.0.1:{port}'
    url = f'http://shared-account.test:{server.server_port}/'
    session = 'accept-' + uuid.uuid4().hex[:12]
    args = [chrome, '--headless=new', '--no-sandbox', '--disable-dev-shm-usage',
            '--disable-extensions', '--no-first-run', '--no-proxy-server',
            '--host-resolver-rules=MAP shared-account.test 127.0.0.1',
            f'--remote-debugging-port={port}', f'--user-data-dir={root / "chrome"}', 'about:blank']
    process = None

    def start():
        nonlocal process
        process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(endpoint + '/json/version', timeout=1):
                    return
            except OSError:
                time.sleep(.1)
        raise RuntimeError('acceptance Chrome did not start')

    def stop():
        process.terminate()
        process.wait(timeout=10)

    def provider():
        return BrowserHarnessProvider(
            runtime=BrowserHarnessRuntime(executable=harness, cdp_url=endpoint,
                                          state_dir=root / 'state', runtime_dir=root / 'harness'),
            policy=BrowserPolicy(timeout_seconds=15),
            dns_resolver=lambda *a, **k: [(2, 1, 6, '', ('93.184.216.34', 80))])

    read = "(async()=>{const d=await new Promise((r,j)=>{const q=indexedDB.open('identity');q.onsuccess=()=>r(q.result);q.onerror=()=>j(q.error)});const value=await new Promise((r,j)=>{const q=d.transaction('tokens').objectStore('tokens').get('login');q.onsuccess=()=>r(q.result);q.onerror=()=>j(q.error)});d.close();return {cookie:document.cookie,local:localStorage.getItem('identity'),idb:value}})()"
    try:
        start()
        primary = provider()
        for name in ['alice', 'bob']:
            account = primary.for_account(name)
            account.invoke(BROWSER_NAVIGATE, {'session_id': session, 'url': url})
            account.evaluate(session, "(async()=>{document.cookie='identity=" + name + ";path=/';localStorage.setItem('identity','" + name + "');const d=await new Promise((r,j)=>{const q=indexedDB.open('identity',1);q.onupgradeneeded=()=>q.result.createObjectStore('tokens');q.onsuccess=()=>r(q.result);q.onerror=()=>j(q.error)});await new Promise((r,j)=>{const t=d.transaction('tokens','readwrite');t.objectStore('tokens').put('" + name + "','login');t.oncomplete=r;t.onerror=()=>j(t.error)});d.close();return true})()")
        for name in ['alice', 'bob']:
            value = primary.for_account(name).evaluate(session, read)
            assert value == {'cookie': 'identity=' + name, 'local': name, 'idb': name}, value
        primary.invoke(BROWSER_NAVIGATE, {'session_id': session, 'url': url})
        assert primary.evaluate(session, 'document.cookie') == ''
        assert primary.evaluate(session, "localStorage.getItem('identity')") is None
        primary.close()
        fresh = provider()
        assert fresh.for_account('alice').evaluate(session, read)['idb'] == 'alice'
        stop()
        start()
        fresh = provider()
        for name in ['alice', 'bob']:
            account = fresh.for_account(name)
            account.invoke(BROWSER_NAVIGATE, {'session_id': session, 'url': url})
            value = account.evaluate(session, read)
            assert value == {'cookie': 'identity=' + name, 'local': name, 'idb': name}, value
        print(json.dumps({'status': 'PASS', 'accounts': 2,
                          'same_site_isolation': True, 'runtime_restart': True,
                          'browser_restart_cookies_local_storage_indexed_db': True,
                          'browser_processes': 1}))
    finally:
        if process is not None and process.poll() is None:
            stop()
        # Shut down only disposable Harness daemons associated with this test.
        import signal
        for path in root.rglob('*.pid'):
            try:
                pid = int(path.read_text().strip())
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        server.shutdown()


if __name__ == '__main__':
    main()
