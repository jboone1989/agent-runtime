# agent-runtime 接入 Agent Engineering Kit (AEK v0.4.1) 设计

日期：2026-10-11
状态：已获用户批准（设计对话逐节确认）
工具仓库：https://github.com/jboone1989/agent-engineering-kit（v0.4.1，main）

## 目标

让 agent-runtime 的模块边界由 AEK 守卫：接管的现有边界执法、开启 strict 模式（policy 变更必须隔离 PR、放宽必须留审计）、boundary 四件套作为守卫行为测试，并收编重复的旧 CI 机制。

## 现状（2026-10-11 探索结论）

- `src/agent_runtime` 约 55 个顶层模块；`package = agent_runtime`，src 布局。
- 已有手写边界执法：`.importlinter` 含 2 条 forbidden contract（`learning`/`memory` 不得依赖 `dashboard`），由 `.github/workflows/architecture-boundaries.yml` 在 CI 跑 `lint-imports`。**两条契约在当前代码上为绿（KEPT）**。
- 另有 `ci.yml`（boundaries job 跑四个边界/契约测试文件 + regression job 全量回归）和 `test.yml`（全量 pytest + packs）。
- cycle 探测：`acyclic_siblings` 在当前代码上 **BROKEN——20 个既有循环依赖**（cognition→action、affect→motivation、capabilities→planning 等）。
- 根目录有手写 `AGENTS.md`（项目规则，kit init 不覆盖）；无 `CLAUDE.md`、无 `ARCHITECTURE.md`。
- 本地工作区有 4 个未提交文件（`src/agent_runtime/llm/deepseek.py`、`memory/local_retrieval.py`、`wakeful/plan_store.py`、`uv.lock`）——接入工作不触碰，提交只 add 指定文件。
- Windows 本地 venv 缺依赖，全量测试约 360 个既有环境性失败（见项目记忆），本地行为测试不可作权威。

## 决策记录

| 决策 | 选择 | 理由 |
|---|---|---|
| strict 模式 | 初装即开 | 用户选定；首 PR 为纯脚手架，满足 strict 隔离白名单；kit 核心价值即防侵蚀 |
| 守卫行为测试 | boundary 四件套 | 快、真实、fail-fast；全量回归由 `ci.yml` regression / `test.yml` 负责，不重复 |
| 旧 CI 收编 | 去重收编 | 删 `.importlinter` + `architecture-boundaries.yml` + `ci.yml` 的 boundaries job；regression 保留 |
| policy 模式 | forbidden pairs（非 allowlist） | 55 个模块全量声明 + coverage 检查初装负担过重；只执法声明的边，现有 2 条契约原样迁入 |
| cycle 检查 | `check_cycles: false` 起步 | 20 个既有循环会立即使 guard 红；治理后开启属收紧，无需 relax 条目 |

## Policy 定义

`aegkit init` 后手改两处（`check_cycles` true→false、追加 `"strict": true`）。首 PR 无 base policy 可比，CI 不视为 relaxation；收紧与初装均无需 relaxations 条目。

```json
{
  "schema_version": 1,
  "language": "python",
  "package": "agent_runtime",
  "source": null,
  "forbidden": [["learning", "dashboard"], ["memory", "dashboard"]],
  "check_cycles": false,
  "tests": [["python", "-m", "pytest", "-q",
             "tests/test_platform_boundaries.py",
             "tests/test_agent_platform_foundation.py",
             "tests/test_extension_governance.py",
             "tests/test_contract_v1.py"]],
  "strict": true
}
```

init 命令形态（在 agent-runtime 根目录执行）：

```bash
aegkit init . --language python --package agent_runtime \
  --forbid learning:dashboard --forbid memory:dashboard \
  --test "python -m pytest -q tests/test_platform_boundaries.py tests/test_agent_platform_foundation.py tests/test_extension_governance.py tests/test_contract_v1.py"
```

已存在的 `AGENTS.md` 会被 init 跳过（NOT OVERWRITTEN，预期行为）；`CLAUDE.md`、`ARCHITECTURE.md`、`.agent-engineering/**`、`engineering-guard.yml` 会生成。

## PR 拆分（strict 隔离规则决定）

strict 隔离检查：**policy.json 发生变更的 PR 只允许触碰 `.agent-engineering/**`、`.github/workflows/engineering-guard.yml`、`ARCHITECTURE.md`**。收编删除的 `.importlinter`、`architecture-boundaries.yml`、`ci.yml` 修改均属 stray 文件，混入 scaffold PR 会 FAIL。故拆两个 PR，顺序 A→B（新机制先上线，与旧机制短暂并存重复跑，无害；随后清理）。

### PR A — scaffold（policy-only）

内容（全部在隔离白名单内）：
- `.agent-engineering/policy.json`（按上文定义）
- `.agent-engineering/guard.py`、`.agent-engineering/managed.json`（kit 生成）
- `.github/workflows/engineering-guard.yml`（kit 生成）——**手改一处**：install 步骤追加 `python -m pip install -e ".[dev]"`（四件套需要包装上，与 `ci.yml` boundaries job 现有装法一致）。此手改导致今后 `aegkit sync` 拒绝覆盖该 workflow（提示手动合并），属 kit 设计内语义。
- `ARCHITECTURE.md`（kit 生成，含 forbidden 表 + ownership TODO 表）

本地验证：`python .agent-engineering/guard.py check --arch-only`（lint-imports 已安装、`learning`/`memory`/`dashboard` 模块存在，预期绿）。提交只 add 上述文件；不碰 4 个脏文件。

CI 效果：engineering-guard 首跑——strict 无 base 仅打印 NOTE（首 PR base 上无 policy，`relaxation_changes({}, policy)` 为空，不构成 naked relaxation）；architecture 绿（两条契约 KEPT）；behavior 四件套在装了 `.[dev]` 的 runner 上跑。

### PR B — cleanup（普通 PR，无 policy 变更，不触发隔离检查）

- 删 `.importlinter`
- 删 `.github/workflows/architecture-boundaries.yml`
- `ci.yml` 删 boundaries job（regression job 原样保留）
- 提交 `CLAUDE.md`（init 生成但内容为 `@AGENTS.md` 一行，白名单外，故留在 B）
- `AGENTS.md` 追加一节"工程门"：完工前跑 `python .agent-engineering/guard.py check`；policy 变更走隔离 PR；放宽走 `aegkit relax`。不改动既有内容。

## 收编后 CI 形态

| Workflow | 职责 | 管理方 |
|---|---|---|
| `engineering-guard.yml` | strict 隔离（CI 传 `--ci-base`）+ 架构边界 + boundary 四件套 | kit（`aegkit sync` 升级） |
| `ci.yml` regression | 全量回归 | 项目自管 |
| `test.yml` | 全量 pytest + packs | 项目自管（与 regression 的既有重复不在本次范围） |

## 验证策略

- 本地（Windows）：权威验证 = `check --arch-only`；四件套本地尽力跑，因 venv 缺依赖允许环境性失败，行为测试以 CI 为准。
- CI：PR A 的 engineering-guard 必须全绿后才合并。
- 回归保障：接入不修改任何 `src/` 代码；删除的旧机制与 guard 能力等价（2 条契约逐字迁移，已验证绿）。

## 升级路径

- kit 升级 = 在 agent-runtime 根目录跑 `aegkit sync .`：更新 vendored `guard.py`（workflow 因含手改 install 行会被拒绝覆盖，需手动合并）。
- strict 防侵蚀立即生效：删审计条目、裸放宽、policy 混代码提交均会被 CI 拒绝；放宽唯一路径 `aegkit relax . --reason ... --<flag>`。

## 非目标

- 不治理 20 个既有 cycle（后续独立工作；开启 cycle 检查时属收紧）。
- 不合并 `ci.yml` 与 `test.yml` 的全量重复。
- 不将 55 模块纳入 allowlist / coverage 管理（后续视治理进度再议，迁移属收紧）。
- 不改动 `AGENTS.md` 既有内容（只追加）。
