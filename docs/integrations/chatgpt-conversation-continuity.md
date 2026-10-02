# ChatGPT conversation continuity

A browser profile retains authentication. A Browser Harness session retains a CDP target binding. Neither is a ChatGPT conversation identity.

The ChatGPT provider now writes a separate conversation journal per profile/session under the Browser Harness state directory. The journal binds the canonical `https://chatgpt.com/c/<id>` URL and records an outstanding turn before Send. Ordinary calls reuse the live page; process replacement or target loss restores the bound URL. A later call finishes an outstanding response before submitting its own turn. Journal replacement fsyncs both file and directory.

Submission recovery is conservative: an ambiguous Send is not blindly repeated. If the first-ever submission loses its target before a conversation URL is captured, the provider cannot safely invent a replacement conversation. This was observed during acceptance and remains an explicit failure.

## Validation on 2026-10-02

- 40 provider and Browser Harness tests passed after merging the Jev browser recovery changes.
- Real supervised job `lrjob_71c0253f04a04843bb15` succeeded.
- Acceptance session `conversation-acceptance-950b0b89a2ad` used conversation `6abf8e36-291c-83e9-959b-ad929e7554bd` for all three turns.
- The second turn ran in a new OS process; the third ran after closing the dedicated target. Both correctly recalled the first turn's disposable nonce.
- Receipt: `~/.local/state/agent-runtime/browser-harness/chatgpt-profile-chatgpt-conversation-acceptance-950b0b89a2ad-conversation.acceptance.json`.
- This acceptance runs after the reported server reboot; it does not inject a server reboot during an in-flight turn.

## Durable wake boundary

The native cognition watcher currently returns `SUBSCRIPTION_REQUIRED`, `subscriptionVerified=false`, and no subscriptions. The Work host must register `events/subscribe` for `cognition.requested`, with `arguments={agent_id:ferro}`, its callback URL, and signing secret. Calling the watcher or displaying its widget does not create that registration. These host capabilities are not exposed through the callable tools in this session.

Consequently, conversation reuse and target recovery have passed real acceptance, while unattended wake into the same long-lived ChatGPT Work conversation has not. The browser provider journal and the Work cognition event channel are separate mechanisms; neither should be reported as proof of the other.
