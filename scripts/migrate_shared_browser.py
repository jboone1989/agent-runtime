"""Migrate disjoint-site login state into an existing Chrome without restarting it.

Run snapshot, import, and cutover separately. Secrets remain in a mode-0700
host backup directory; stdout contains only counts and identity verification.
The source browser is retired only after destination authentication succeeds.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import urllib.request
from urllib.parse import urlsplit

from websockets.sync.client import connect


class CDP:
    def __init__(self, port):
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/version') as r:
            endpoint = json.load(r)['webSocketDebuggerUrl']
        self.ws = connect(endpoint, max_size=40_000_000).__enter__()
        self.seq = 0
        self.events = []

    def send(self, method, params=None, session=None):
        self.seq += 1
        body = {'id': self.seq, 'method': method, 'params': params or {}}
        if session:
            body['sessionId'] = session
        self.ws.send(json.dumps(body))
        return self.seq

    def receive(self, ident, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            body = json.loads(self.ws.recv(timeout=max(.1, deadline-time.monotonic())))
            if body.get('id') == ident:
                if 'error' in body:
                    raise RuntimeError(body['error']['message'])
                return body.get('result', {})
            self.events.append(body)
        raise TimeoutError('CDP response timed out')

    def call(self, method, params=None, session=None):
        return self.receive(self.send(method, params, session))

    def attach(self, target):
        return self.call('Target.attachToTarget', {'targetId': target, 'flatten': True})['sessionId']

    def evaluate(self, session, expression):
        result = self.call('Runtime.evaluate', {'expression': expression,
                           'returnByValue': True, 'awaitPromise': True}, session)
        if result.get('exceptionDetails'):
            raise RuntimeError('Page storage/identity evaluation failed')
        return result['result'].get('value')

    def pages(self):
        return [r for r in self.call('Target.getTargets')['targetInfos'] if r['type'] == 'page']


def private_write(path, body):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_suffix('.tmp')
    with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as f:
        json.dump(body, f)
    os.replace(temp, path)


def storage_expression(action, state=None):
    source = (Path(__file__).resolve().parents[1] / 'src/agent_runtime/integrations/browser_storage.js').read_text()
    suffix = 'collect(true)' if action == 'collect' else 'restore(' + json.dumps(state) + ')'
    return '(async()=>{const module={exports:{}};' + source + ';return await new StorageScript(false).' + suffix + ';})()'


def capture(client):
    pages = client.pages()
    origins = {}
    for row in pages:
        if not row['url'].startswith(('https://', 'http://')):
            continue
        parsed = urlsplit(row['url'])
        if parsed.scheme + '://' + parsed.netloc in origins:
            continue
        session = client.attach(row['targetId'])
        try:
            origin = client.evaluate(session, 'location.origin')
            origins[origin] = {'origin': origin, **client.evaluate(session, storage_expression('collect'))}
        finally:
            client.call('Target.detachFromTarget', {'sessionId': session})
    return {'cookies': client.call('Storage.getCookies')['cookies'], 'origins': list(origins.values()), 'pages': pages}


def identities(client):
    rows = client.pages()
    chat = next((r for r in rows if urlsplit(r['url']).hostname == 'chatgpt.com'), None)
    x = next((r for r in rows if urlsplit(r['url']).hostname == 'x.com'), None)
    result = {}
    if chat:
        session = client.attach(chat['targetId'])
        try:
            result['chatgpt_authenticated'] = client.evaluate(session, "(async()=>{const r=await fetch('/api/auth/session');const s=await r.json();return r.ok&&!!s.user&&!!s.accessToken})()")
        finally:
            client.call('Target.detachFromTarget', {'sessionId': session})
    if x:
        session = client.attach(x['targetId'])
        try:
            result['x_handle'] = client.evaluate(session, "document.querySelector('[data-testid=AppTabBar_Profile_Link]')?.getAttribute('href')?.replace(/^\\//,'')||null")
            result['x_authenticated'] = client.evaluate(session, "!!document.querySelector('[data-testid=SideNav_AccountSwitcher_Button]')")
        finally:
            client.call('Target.detachFromTarget', {'sessionId': session})
    return result


def restore_origin(client, origin):
    target = client.call('Target.createTarget', {'url': 'about:blank', 'background': True})['targetId']
    session = client.attach(target)
    try:
        client.call('Page.enable', session=session)
        client.call('Network.setBypassServiceWorker', {'bypass': True}, session)
        client.call('Fetch.enable', {'patterns': [{'urlPattern': '*'}]}, session)
        nav = client.send('Page.navigate', {'url': origin['origin'] + '/'}, session)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            body = json.loads(client.ws.recv(timeout=max(.1, deadline-time.monotonic())))
            if body.get('method') == 'Fetch.requestPaused' and body.get('sessionId') == session:
                client.call('Fetch.fulfillRequest', {'requestId': body['params']['requestId'], 'responseCode': 200,
                            'responseHeaders': [{'name': 'Content-Type', 'value': 'text/html'}],
                            'body': 'PGh0bWw+PGJvZHk+PC9ib2R5PjwvaHRtbD4='}, session)
                break
            client.events.append(body)
        else:
            raise TimeoutError('Migration origin restore interception timed out')
        for _ in range(30):
            if client.evaluate(session, 'location.origin') == origin['origin']:
                client.evaluate(session, storage_expression('restore', origin))
                return
            time.sleep(.1)
        raise RuntimeError('Migration origin restore did not load')
    finally:
        client.call('Target.closeTarget', {'targetId': target})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['snapshot', 'import', 'verify', 'cutover'])
    parser.add_argument('--state-root', type=Path, required=True)
    parser.add_argument('--backup', type=Path, required=True)
    parser.add_argument('--destination-profile-dir', type=Path, required=True)
    parser.add_argument('--expected-x-handle', required=True)
    args = parser.parse_args()
    root, backup = args.state_root, args.backup
    profiles = root / 'browser/profiles'
    destination_state = json.loads((profiles / 'chatgpt/runtime.json').read_text())
    destination = CDP(destination_state['cdp_port'])
    if args.stage == 'snapshot':
        source_state = json.loads((profiles / 'x/runtime.json').read_text())
        if source_state['chrome_pid'] == destination_state['chrome_pid']:
            raise RuntimeError('Profiles are already merged')
        source = CDP(source_state['cdp_port'])
        before = {'destination': identities(destination), 'source': identities(source)}
        if before['source'].get('x_handle', '').casefold() != args.expected_x_handle.casefold() or not before['source'].get('x_authenticated'):
            raise RuntimeError('Source X identity did not match')
        if not before['destination'].get('chatgpt_authenticated'):
            raise RuntimeError('Destination ChatGPT is not authenticated')
        private_write(backup / 'source.json', capture(source))
        private_write(backup / 'destination.json', capture(destination))
        private_write(backup / 'runtime-states.json', {'source': source_state, 'destination': destination_state})
        private_write(backup / 'identities.json', before)
        bindings = {str(p.relative_to(root)): json.loads(p.read_text()) for p in (root / 'browser-harness').glob('*.json')}
        private_write(backup / 'bindings.json', bindings)
        print(json.dumps({'stage': 'snapshot', 'verified': before, 'backup_complete': True}))
    elif args.stage == 'import':
        source = json.loads((backup / 'source.json').read_text())
        prior = json.loads((backup / 'destination.json').read_text())
        # Chrome also stores browser-level Google/extension cookies unrelated
        # to the source website. Migrate only cookies belonging to its origins.
        hosts = {urlsplit(row['origin']).hostname for row in source['origins']}
        source['cookies'] = [row for row in source['cookies']
                             if any(host == row['domain'].lstrip('.') or
                                    host.endswith('.' + row['domain'].lstrip('.')) for host in hosts)]
        primary_origins = {r['origin'] for r in prior['origins']}
        if primary_origins.intersection(r['origin'] for r in source['origins']):
            raise RuntimeError('Overlapping origins require isolated account migration')
        cookie_key = lambda r: (r['name'], r['domain'], r['path'], json.dumps(r.get('partitionKey'), sort_keys=True))
        primary_cookies = {cookie_key(r): r for r in prior['cookies']}
        if any(cookie_key(r) in primary_cookies and primary_cookies[cookie_key(r)]['value'] != r['value'] for r in source['cookies']):
            raise RuntimeError('Conflicting cookies require isolated account migration')
        fields = {'name', 'value', 'domain', 'path', 'expires', 'httpOnly', 'secure', 'sameSite', 'priority', 'sourceScheme', 'sourcePort', 'partitionKey'}
        destination.call('Storage.setCookies', {'cookies': [{k: v for k, v in r.items() if k in fields} for r in source['cookies']]})
        for origin in source['origins']:
            restore_origin(destination, origin)
        urls = {}
        mapping = {}
        for page in source['pages']:
            if not page['url'].startswith(('http://', 'https://')):
                continue
            if page['url'] not in urls:
                urls[page['url']] = destination.call('Target.createTarget', {'url': page['url'], 'background': True})['targetId']
            mapping[page['targetId']] = urls[page['url']]
        private_write(backup / 'target-map.json', mapping)
        print(json.dumps({'stage': 'import', 'cookies_copied': len(source['cookies']), 'origins_copied': len(source['origins']), 'site_tabs': len(urls)}))
    else:
        deadline = time.monotonic() + 20
        while True:
            verified = identities(destination)
            if verified.get('chatgpt_authenticated') and verified.get('x_authenticated') and verified.get('x_handle', '').casefold() == args.expected_x_handle.casefold():
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('Destination identities did not verify: ' + json.dumps(verified))
            time.sleep(1)
        if args.stage == 'cutover':
            states = json.loads((backup / 'runtime-states.json').read_text())
            old = states['source']
            if old['chrome_pid'] == destination_state['chrome_pid']:
                raise RuntimeError('Refusing to retire the destination browser')
            subprocess.run(['systemctl', '--user', 'disable', '--now', 'agent-runtime-browser-x.service'], check=True, capture_output=True)
            mapping = json.loads((backup / 'target-map.json').read_text())
            for name, binding in json.loads((backup / 'bindings.json').read_text()).items():
                target = binding.get('target_id')
                if target in mapping:
                    binding['target_id'] = mapping[target]
                    binding['browser_binding'] = 'http://127.0.0.1:' + str(destination_state['cdp_port'])
                    private_write(root / name, binding)
            private_write(root / 'browser/shared.json', {'profile_id': 'chatgpt', 'profile_dir': str(args.destination_profile_dir)})
            private_write(profiles / 'x/runtime.json', destination_state)
            # Scoped retirement of the source stack only; the primary browser
            # remains alive and keeps its existing conversations and tabs.
            for field in ['websockify_pid', 'chrome_pid', 'window_manager_pid', 'desktop_pid']:
                pid = old.get(field)
                if pid:
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
            private_write(backup / 'cutover-receipt.json', {'verified': verified, 'destination_pid': destination_state['chrome_pid'], 'retired_pid': old['chrome_pid']})
        print(json.dumps({'stage': args.stage, 'verified': verified, 'chrome_pid': destination_state['chrome_pid'], 'cdp_port': destination_state['cdp_port']}))


if __name__ == '__main__':
    main()
