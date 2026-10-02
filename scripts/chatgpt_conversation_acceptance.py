"""Prove conversation continuity across fresh OS processes and a closed tab."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_runtime.llm.chatgpt_browser import ChatGPTBrowserProvider, _BrowserProfileProcessLock
from agent_runtime.integrations.browser import BROWSER_NAVIGATE


def child(prompt: str) -> dict:
    provider = ChatGPTBrowserProvider.from_environment()
    response = asyncio.run(provider.complete(prompt))
    return {"response": response, "metadata": provider.last_metadata}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--child-prompt")
    args = parser.parse_args()
    if args.child_prompt:
        print(json.dumps(child(args.child_prompt), ensure_ascii=False))
        return
    session = "conversation-acceptance-" + uuid.uuid4().hex[:12]
    env = {**os.environ, "AGENT_RUNTIME_CHATGPT_SESSION_ID": session}
    os.environ["AGENT_RUNTIME_CHATGPT_SESSION_ID"] = session
    provider = ChatGPTBrowserProvider.from_environment()
    # Own a distinct target so the tab-close check cannot close Ferro's tab.
    with _BrowserProfileProcessLock(provider.process_lock_path, timeout_seconds=225):
        provider.browser.invoke(BROWSER_NAVIGATE, {"session_id": session, "url": provider.chat_url})
    print(json.dumps({"stage": "dedicated_tab_ready", "session_id": session}), flush=True)
    marker = "continuity-" + uuid.uuid4().hex
    def run(prompt):
        result = subprocess.run([sys.executable, __file__, "--child-prompt", prompt],
                                env=env, capture_output=True, text=True)
        if result.returncode:
            print(result.stdout, file=sys.stderr, flush=True)
            print(result.stderr, file=sys.stderr, flush=True)
            raise RuntimeError(f"Conversation acceptance child exited {result.returncode}")
        return json.loads(result.stdout.splitlines()[-1])
    first = run(f"This is a disposable conversation-context test. Do not create or update saved memories. For this conversation only, the nonce is {marker}. Reply only ACK.")
    print(json.dumps({"stage": "first_turn", "metadata": first["metadata"]}), flush=True)
    second = run("Using only the preceding messages in this conversation, return the exact nonce. Do not create or update saved memories.")
    print(json.dumps({"stage": "second_process", "metadata": second["metadata"]}), flush=True)
    with _BrowserProfileProcessLock(provider.process_lock_path, timeout_seconds=225):
        provider.browser.run_session_script(session, "close_tab(target_id)\nresult = {'closed': True}", timeout_seconds=30)
    third = run("Using only the first user message in this conversation, return the exact nonce. Do not create or update saved memories.")
    urls = [item["metadata"]["url"] for item in [first, second, third]]
    passed = len(set(urls)) == 1 and marker in second["response"] and marker in third["response"]
    receipt = {"status": "PASS" if passed else "FAIL", "session_id": session,
               "same_conversation_after_process_restart": urls[0] == urls[1],
               "same_conversation_after_tab_close": urls[1] == urls[2],
               "history_recalled": marker in second["response"] and marker in third["response"],
               "turns": [first, second, third]}
    path = provider.conversation_state_path.with_suffix(".acceptance.json")
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(receipt, ensure_ascii=False))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
