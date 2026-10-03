"""Account scope executed inside Browser Harness, using its CDP transport.

The caller supplies account_id, account_path, browser_binding and storage_source.
No browser process is created here. Default-profile data stays Chrome-owned.
"""
import json
import os
from pathlib import Path
import time
from concurrent.futures import ThreadPoolExecutor
from browser_harness.helpers import _send


def _write_account(value):
    account_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = account_path.with_suffix('.tmp')
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as handle:
        json.dump(value, handle, separators=(',', ':'))
    os.replace(tmp, account_path)


def _page_js(session, expression):
    body = cdp('Runtime.evaluate', session_id=session, expression=expression,
               returnByValue=True, awaitPromise=True, _response_timeout=30.0)
    if body.get('exceptionDetails'):
        raise RuntimeError('account storage operation failed: ' + str(body['exceptionDetails']))
    return body['result'].get('value')


def _storage_expression(action, state=None):
    suffix = 'collect(true)' if action == 'collect' else 'restore(' + json.dumps(state) + ')'
    return "(async()=>{const module={exports:{}};" + storage_source + ";return await new StorageScript(false)." + suffix + ";})()"


def _restore_origin(context_id, origin):
    # Restore on an intercepted empty document in this exact context. Do not
    # navigate an authenticated/task page or execute a website during restore.
    target = cdp('Target.createTarget', url='about:blank', browserContextId=context_id)['targetId']
    session = cdp('Target.attachToTarget', targetId=target, flatten=True)['sessionId']
    try:
        cdp('Page.enable', session_id=session)
        cdp('Fetch.enable', session_id=session, patterns=[{'urlPattern': '*'}])
        with ThreadPoolExecutor(max_workers=1) as worker:
            navigation = worker.submit(cdp, 'Page.navigate', session_id=session,
                                       url=origin['origin'] + '/', _response_timeout=20.0)
            deadline = time.monotonic() + 15
            fulfilled = False
            while time.monotonic() < deadline:
                for event in drain_events():
                    if event.get('session_id') == session and event.get('method') == 'Fetch.requestPaused':
                        cdp('Fetch.fulfillRequest', session_id=session,
                            requestId=event['params']['requestId'], responseCode=200,
                            responseHeaders=[{'name': 'Content-Type', 'value': 'text/html'}],
                            body='PGh0bWw+PGJvZHk+PC9ib2R5PjwvaHRtbD4=')
                        fulfilled = True
                if fulfilled and _page_js(session, 'location.origin') == origin['origin']:
                    navigation.result(timeout=5)
                    _page_js(session, _storage_expression('restore', origin))
                    return
                time.sleep(0.05)
        raise TimeoutError('account origin restoration timed out: ' + origin['origin'])
    finally:
        cdp('Target.closeTarget', targetId=target)


_browser_session = None
_contexts = cdp('Target.getBrowserContexts')
_original_switch_tab = switch_tab
_daemon_tab = current_tab()
_daemon_target = (_daemon_tab.get('targetId') or _daemon_tab.get('target_id')) if isinstance(_daemon_tab, dict) else _daemon_tab
_account_lock = None
_account_state = {}
if account_id == 'default':
    account_context = _contexts.get('defaultBrowserContextId')
    if browser_engine == "lightpanda":
        # Lightpanda represents the default context by omitting its ID.
        account_context = None
    elif not account_context:
        raise RuntimeError('Chrome did not expose its default browser context')
else:
    _browser_session = cdp('Target.attachToBrowserTarget')['sessionId']
    account_path = Path(account_path)
    account_path.parent.mkdir(parents=True, exist_ok=True)
    _account_lock = account_path.with_suffix('.lock').open('a+b')
    if os.name == 'nt':
        import msvcrt
        _account_lock.write(b'0')
        _account_lock.flush()
        _account_lock.seek(0)
        msvcrt.locking(_account_lock.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl
        fcntl.flock(_account_lock.fileno(), fcntl.LOCK_EX)
    if account_path.exists():
        _account_state = json.loads(account_path.read_text())
        if _account_state.get('browser_binding') != browser_binding:
            raise RuntimeError('account is bound to a different shared browser')
    account_context = _account_state.get('context_id')
    if account_context not in _contexts['browserContextIds']:
        account_context = cdp('Target.createBrowserContext', disposeOnDetach=False)['browserContextId']
        storage = _account_state.get('storage') or {'cookies': [], 'origins': []}
        cookies = [{k: v for k, v in row.items() if k in {'name','value','domain','path','expires','httpOnly','secure','sameSite','priority','sameParty','sourceScheme','sourcePort','partitionKey'}} for row in storage['cookies']]
        if cookies:
            cdp('Storage.setCookies', session_id=_browser_session,
                browserContextId=account_context, cookies=cookies)
        for origin in storage['origins']:
            _restore_origin(account_context, origin)
        _account_state = {**_account_state, 'account_id': account_id,
                          'browser_binding': browser_binding, 'context_id': account_context}
        _write_account(_account_state)


def list_tabs(include_chrome=True):
    rows = cdp('Target.getTargets')['targetInfos']
    return [row for row in rows if row['type'] == 'page'
            and row.get('browserContextId') == account_context
            and (include_chrome or not row.get('url', '').startswith(('chrome:', 'devtools:', 'chrome-extension:')))]


def current_session_id():
    session = _send({'meta': 'session'}).get('session_id')
    if not session:
        raise RuntimeError('Browser Harness has no attached page session')
    return session


def switch_tab(target, activate=False):
    row = cdp('Target.getTargetInfo', targetId=target)['targetInfo']
    if row.get('browserContextId') != account_context:
        raise RuntimeError('browser target belongs to another account')
    old_tab = current_tab()
    old_target = (old_tab.get('targetId') or old_tab.get('target_id')) if isinstance(old_tab, dict) else old_tab
    old_session = current_session_id()
    if old_target == target:
        if activate:
            cdp('Target.activateTarget', targetId=target)
        return old_session
    new_session = _original_switch_tab(target, activate=activate)
    # Harness disables the previous Network domain but leaves that session
    # attached. Release its Runtime/DOM agents and retained remote objects.
    cdp('Target.detachFromTarget', sessionId=old_session)
    return new_session


def new_tab(url='about:blank'):
    # A named Harness daemon creates its own blank tab before execution. Reuse
    # that exact tab; never reclaim an operator's unrelated blank tab.
    rows = {row['targetId']: row for row in list_tabs()}
    blank = rows.get(_daemon_target)
    if blank and blank.get('url') == 'about:blank':
        target = _daemon_target
    else:
        params = {'url': 'about:blank', 'background': True}
        if account_id != 'default':
            params['browserContextId'] = account_context
        target = cdp('Target.createTarget', **params)['targetId']
    switch_tab(target)
    if url != 'about:blank':
        goto_url(url)
    return target


def checkpoint_account():
    if account_id == 'default':
        return
    cookies = cdp('Storage.getCookies', session_id=_browser_session,
                  browserContextId=account_context)['cookies']
    origins = {row['origin']: row for row in (_account_state.get('storage') or {}).get('origins', [])}
    for row in list_tabs(include_chrome=False):
        if not row.get('url', '').startswith(('http://', 'https://')):
            continue
        session = cdp('Target.attachToTarget', targetId=row['targetId'], flatten=True)['sessionId']
        try:
            origin = _page_js(session, 'location.origin')
            if origin and origin != 'null':
                origins[origin] = {'origin': origin, **_page_js(session, _storage_expression('collect'))}
        finally:
            cdp('Target.detachFromTarget', sessionId=session)
    _account_state['storage'] = {'cookies': cookies, 'origins': list(origins.values())}
    _write_account(_account_state)


def release_account_lock():
    try:
        # Reclaim only this daemon's own unused startup tab.
        if _daemon_target:
            rows = {row['targetId']: row for row in cdp('Target.getTargets')['targetInfos']}
            if rows.get(_daemon_target, {}).get('url') == 'about:blank':
                cdp('Target.closeTarget', targetId=_daemon_target)
    finally:
        try:
            if _browser_session is not None:
                cdp('Target.detachFromTarget', sessionId=_browser_session)
        finally:
            if _account_lock is not None:
                _account_lock.close()
