from pathlib import Path

import pytest

from agent_runtime.integrations.browser_harness import BrowserHarnessProvider, BrowserHarnessRuntime
from test_browser_takeover_integration import _provider


def test_adding_profiles_reuses_chrome_desktop_and_takeover(tmp_path):
    provider, host = _provider(tmp_path)
    first = provider.start_takeover('chatgpt')
    processes = set(host.processes)
    second = provider.start_takeover('x')
    third = provider.start_profile('second-x-account')
    assert set(host.processes) == processes
    assert first.cdp_url == second.cdp_url == third.cdp_url
    assert first.websockify_pid == second.websockify_pid
    assert len([a for a in host.argv.values() if any(x.startswith('--user-data-dir=') for x in a)]) == 1


def test_account_target_state_is_separate_and_endpoint_shared(tmp_path):
    provider = BrowserHarnessProvider(runtime=BrowserHarnessRuntime(
        executable='/bin/true', cdp_url='http://127.0.0.1:9600', state_dir=tmp_path))
    a, b = provider.for_account('alice'), provider.for_account('bob')
    a._save_target_id('x', 'alice-tab')
    b._save_target_id('x', 'bob-tab')
    assert a._load_target_id('x') == 'alice-tab'
    assert b._load_target_id('x') == 'bob-tab'
    assert a.runtime.cdp_url == b.runtime.cdp_url == provider.runtime.cdp_url
    assert provider.for_account('alice') is a
    provider.close()
    with pytest.raises(RuntimeError, match='closed'):
        a.invoke('browser.inspect')


def test_isolated_account_cannot_launch_another_browser():
    with pytest.raises(ValueError, match='shared browser endpoint'):
        BrowserHarnessRuntime(account_id='second-account')
