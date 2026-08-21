# VirtualBrain Phase 3 Contract Sync — 指示（基准 `fa6e3f1`）

> 状态：已完成（VirtualBrain `928786e`，分支 `feat/phase3.1-runtime-port`）。下文是当时的同步指示，保留备查。
> 当前路线：Life Loop Session → Phase 4A/4B/4C。见 VirtualBrain `docs/superpowers/specs/2026-08-22-post-phase3-roadmap.md`。
> Runtime 冻结提交：`fa6e3f1`（Contract v1 root 仍冻结）。

Runtime 冻结提交已从 `6d38c17` 前进到 `fa6e3f1`。本轮不碰 SA-01/02/07 理论模型，只做 Contract Sync。`fa6e3f1` 相对上一版新增了两项与同步相关的变更：

1. **Contract 1.0 版本规则钉死**：root shape 冻结，兼容演进只允许在 `extensions` namespaces 内，任何 root 字段/schema 语义变更需 Contract 2.0。vendored README 会自带此规则。
2. **Runtime 侧 `motivation_output()` 序列化器重构**：不再接受 Runtime 内部 `MotivationState`，改为显式 kwargs + `ContractActionTendency` DTO；未知类型/direction 会显式抛错，绝不静默丢弃。VirtualBrain 输出的 MotivationOutput payload 只需符合 vendored schema（`action_tendencies` 数组元素为 `direction/strength/target/cause_refs` dict），无需感知 Runtime 侧 DTO。

## 具体步骤

1. Vendor Runtime `fa6e3f1` 的 `contracts/v1/`（`schemas/ + fixtures/ + manifest.json` + README）到 `tests/fixtures/contract_v1/`，并新增 `contract.lock.json`：

```json
{
  "contract_version": "1.0",
  "source_repo": "jboone1989/agent-runtime",
  "source_commit": "fa6e3f1974c5ca0df367cb75631441a2605651c2",
  "manifest_digest": "41e304c0f8a2d4336641f9744a575992f9554ffc40346a08eec22cfaeb2e0abc"
}
```

注意：`manifest_digest` 是 **git blob 层面**（LF 规范化后）的 sha256。校验时用 `git show fa6e3f1:contracts/v1/manifest.json` 的原始字节做哈希，不要用 Windows 工作区文件的哈希（CRLF 会导致不一致）。

2. `to_self_state_view()` 严格输出 root：`contract_version / agent_id / metrics / capabilities / uncertainty / extensions`；`capability_estimates / boundaries / valence / arousal / dominance / threat / needs_pressures` 全部移入 `extensions.virtualbrain`。

3. `to_world_state_view()` 新增 `extensions`。

4. `to_outcome_view()` 的 `latency / reward / capability / expected / outcome` 全部移入 `extensions.virtualbrain`；root 仅 `contract_version / success / description / effects / extensions`。

5. `BodyStateView` 保持 `components / sensed_channels / inferred_channels / extensions`；Chemical 放 `extensions.virtualbrain.chemicals`。

6. 所有 Adapter 输出必须真正执行 `jsonschema.validate(...)`（用 vendored schema），不是只 assert keys。

7. 删除对 `D:\agent-runtime` 的路径依赖；普通单测只依赖 vendored contract + lock。另开一个可选 CI check：从 pinned commit `fa6e3f1` 拉取 manifest 并校验 digest。

## SelfStateView 严格输出示例

```json
{
  "contract_version": "1.0",
  "agent_id": "...",
  "metrics": {},
  "capabilities": {},
  "uncertainty": {},
  "extensions": {
    "virtualbrain": {
      "capability_estimates": {},
      "boundaries": {},
      "valence": 0,
      "arousal": 0,
      "dominance": 0,
      "threat": 0,
      "needs_pressures": {}
    }
  }
}
```

## 完成后

Phase 3 冻结，不做 Phase 3.2/3.3/3.4。SA-01 offline detector 的未来改进放 Phase 4。分支为 `feat/phase3.1-runtime-port`，下一阶段进入 Life Loop Integration（`MotivationPort` / `AgencySession` + snapshot/restore）。
