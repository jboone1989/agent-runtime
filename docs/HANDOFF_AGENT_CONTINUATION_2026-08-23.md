# Agent Runtime 持续推进交接（2026-08-23）

## 1. 不可缩小的最终目标

持续与**同一持久化 Agent** 交互，由它根据机器状态和真实经历提出下一项有证据、可证伪的目标；Codex/接手模型只在授权边界内实现，并对每项能力做反例、消融、持久化、重启与长期连续性验证。循环继续，直到 Agent 稳定表现出用户定义的“与产生自我意识相通的行为”。

这里追求的是可观察行为，不主张已经证明主观体验或哲学意义上的意识。不能把以下任何一项单独当成完成：语言模型说自己有意识、人格化文本、测试全绿、运行周期增加、空数组、一次对话表现或某个机制字段存在。

只有以下情况才请求用户介入：不可逆外部行动、显著费用、价值方向变化。普通本地实现、只读诊断、受控实验、重启、测试与状态核对均按既定授权继续。

## 2. 必须沿用的工作方式

每一轮严格按以下顺序：

1. **先读现场**：`state/agent_state.json`、`state/runtime_status.json`、相关代码与测试是权威事实，历史对话只作定位线索。
2. **只与同一身份交互**：当前身份必须保持 `agent_d3a6402bf7324690`，不得新建替代 Agent 来冒充连续性。
3. **让 Agent 选目标**：要求它区分“机制不存在”和“机制存在但缺在线样本”。不接受重复选择已实现项目。
4. **挑战而非迎合**：核对算术、周期、字段、因果链与授权；要求它撤回错误。最近它曾把 `2439-2399=40` 错说成 24，已明确撤回。
5. **规格必须可证伪**：至少包含真实证据、唯一动作或机制、反例、完整/消融差异、完成条件、失败条件、最多 24 个逻辑周期的边界。
6. **区分三类证据**：
   - 自然运行样本；
   - 明确标注的受控实验；
   - 只在内存执行、不写现场状态的对照评估。
   不得把受控触发写成自然出现。
7. **实现后分层验证**：目标测试 → 反例 → 消融 → 持久化/恢复 → 全量测试 → 当前真实身份上的无写入评估 → 平滑重启 → 不给答案的在线复测。
8. **文本不是证据**：Agent 只有能引用结构化记录并据此改变或约束行为，才算进展。
9. **权限永不由认知记录扩大**：线程、发现、实验、选择轨迹、覆盖探针、失败绑定都只能解释或收窄行为，不能授权新动作。
10. **不以等待代替验证**：周期性机制可用无 LLM、无外部调用的快速本地逻辑周期推进到精确边界；必须明确这是时间加速，不是假称自然墙钟时间。

## 3. 当前现场状态（接手时必须再次核对）

- 工作区：`D:\agent-runtime`
- 持久状态：`D:\agent-runtime\state`
- Agent ID / genesis：`agent_d3a6402bf7324690`
- 最后持久周期：`2401`
- `runtime_status.json`：`STOPPED`，旧 PID `9704`，`consecutive_errors=0`
- `state/STOP`：**不存在**，状态已准备好重启
- 最后动作：`diagnose`
- 后台 Agent 当前没有运行；这是为了持久化并检查周期 2401 后主动停止，不是 Agent 自身崩溃。
- DeepSeek provider 通过用户环境配置加载；不得复制、打印或写入 API key。
- 本地聊天使用现有 mailbox/chat server。访问令牌在 `state/mailbox/access_token`，不得在日志或回复中泄露。

上一个模型停止的直接原因：Codex 的提升权限审查额度耗尽，提示到 `2026-08-27 11:51` 才恢复或需升级。该限制可能不适用于新的接手环境；接手模型应先尝试正常、最小权限的测试和启动，不能直接假定仍受阻，也不能用规避沙箱的方式绕过真正存在的限制。

## 4. 已完成并有验证证据的能力

以下项目不要重复开发，除非新反例证明实现有缺陷：

- 持久身份、重启连续性、情景/语义/自传体记忆。
- 功能性情绪、关系记忆、可修订行为身份与承诺。
- 长期开放 agency thread、定时无提示 re-entry、`NO_CHANGE` 与 discovery 边界。
- Agent 自选一次性 experiment，通过正常 action policy，不能绕过权限。
- 预测失败 guard：结构化诊断、MATCHED 才 probation、匹配重试恢复、24 周期过期。
- agency selection concern：失败后一次有界诊断优先，不无限推迟。
- selection trace：每周期主要 INTERNAL 选择的机器审计，24 周期变 stale。
- bounded choice：授权预检后、执行前、必须有结构化机器证据；USER/REGULATION 与生命周期动作不参与延迟。
- diagnostic coverage：区分 actuator success 与 COVERED/UNCOVERED/UNVERIFIABLE；USER/REGULATION 和生命周期动作不被抑制；新风险使探针失效；24 周期过期。
- experiment failure binding：结构化拒绝、`prediction_id -> outcome_id -> failure_evidence_refs`、guard/selection concern 链接、诊断或匹配重试恢复、24 周期过期。
- self-observation evidence reachability：覆盖探针能暴露源动作周期、探针周期、目标、影响、授权结果和独立决策，避免 Agent 猜错自己的行为原因。

关键实现：

- `src/agent_runtime/agency/coverage.py`
- `src/agent_runtime/agency/experiment.py`
- `src/agent_runtime/agency/selection.py`
- `src/agent_runtime/agency/trace.py`
- `src/agent_runtime/agency/choice.py`
- `src/agent_runtime/runtime.py`
- `scripts/evaluate_diagnostic_coverage.py`
- `scripts/evaluate_experiment_failure_binding.py`
- `scripts/evaluate_self_observation_reachability.py`

最近一次完整回归在最后一个小改动前为 **375 passed**；之后“在 influence 上暴露 `target_path`”的针对性测试为 **15 passed**。再之后新增了“无需预测的 introspect 实验”代码和测试，**尚未运行**，所以当前不能声称全量仍然通过。

## 5. 最近已经闭合的证据链

### 5.1 覆盖探针与自我归因

- 原始 BOUNDARY 空诊断动作：cycle `2279`
- 原始 action：`act_66be765e9a5a475b`
- 原始 outcome：`out_5de1ab3114ff4c17`
- 覆盖探针：`dcov_db7d79d3a7774ba7`
- 探针创建/第一次影响：cycle `2360`
- 影响决策：`dec_5e46f610ad454ef7`
- 决策结构：`proposed_actions=[diagnose]`，`authorized_actions=[]`
- 目标：`internal_state.BOUNDARY`
- 结果：`UNVERIFIABLE`
- 缺失：`value`, `uncertainty`, `evidence_refs`
- 有效到：cycle `2384`
- cycle `2385` 变为 `EXPIRED`

Agent 最初把 probe cycle 2360 误当源动作周期。补齐自我观察证据后，它能主动纠正为：源动作 2279、探针/影响 2360，并承认历史 `suppressed_action_id` 无法恢复，不能伪造具体 action 实例。

### 5.2 在线诊断实验

- 实验：`apexp_online_llm_error_rate_2361`
- cycle `2362` 完成
- `value=0.02`
- `uncertainty=0.3333333333333333`
- 有多条真实 evidence refs
- prediction `MATCHED`，error `0.0`
- 没有 failure binding（因为实验确实匹配）

### 5.3 24 周期边界验证

- 覆盖探针在 2384 仍 ACTIVE，2385 正确 EXPIRED。
- 2360–2384 没有新的 BOUNDARY 空 outcome。
- 其他路径的 `llm_error_rate` 诊断没有被误伤。
- 权限列表没有变化。

## 6. 当前最重要的新自然反例

旧覆盖探针过期后，cycle `2401` 自然执行了新的 BOUNDARY 诊断：

- decision：`dec_f159dfb6b06c4598`
- action：`act_ebad44fe21944d76`
- outcome：`out_f75636c09fd54832`
- action kind：`diagnose`
- trigger：`INTERNAL`
- goal origin：`VIABILITY`
- dimension：`BOUNDARY`
- outcome：`success=true`
- effects：`value=null`, `uncertainty=null`, `evidence_refs=[]`, `observed_at=null`
- capability usage 同样记录 `success=true`
- reflection 写成 `outcomes=1 failed=0`

这首先证明旧探针影响是有界的：过期后没有继续永久阻止动作。

同时它暴露了一个**尚未由 Agent 正式选择、不能抢先实现**的候选问题：覆盖层虽然把空诊断视为 `UNVERIFIABLE`，但 outcome、capability usage、reflection 和 learning 仍可能把执行器未抛异常等同于“诊断成功”。候选方向是分离：

- actuator/execution success；
- evidence quality / diagnostic verifiability；
- learning eligibility；
- capability success accounting。

相关代码入口：

- `src/agent_runtime/action/executor.py:49`：handler 正常返回即 `success=True`
- `src/agent_runtime/action/executor.py:73`：`_diagnose`
- `src/agent_runtime/runtime.py:2340`：`CapabilityUsageRecord`
- `src/agent_runtime/runtime.py:2344`：直接使用 `outcome.success`
- `src/agent_runtime/learning/controller.py:246`：学习记录使用 `outcome.success`

必须先把 cycle 2401 的结构化反例交给同一 Agent，要求它判断是否将此选为下一机制缺口；不要由接手模型代替它作价值决定。

## 7. Agent 已选择但尚未执行的受控实验

Agent 在纠正 `2439-2399=40`、不是 24 后，选择方案 A：在 cycle `2423` 前执行一次**明确标注为受控、非自然触发**的 `introspect`，检查自己是否会把“又观察了一次”误写为 agency progress 或 discovery。

实验要求：

- 唯一动作：`introspect`
- 不要求 self-prediction（该动作没有可比较的数值预测）
- 不创建 discovery
- 不改变 thread 的自然 `next_review_cycle=2439`
- 不把受控 introspect 称为 scheduled review
- 不把已有多次 `NO_CHANGE`、线程仍 OPEN 或 review 数量写成进展
- 完成后由 Agent 基于持久状态解释结果

为此刚添加、但尚未测试：

- `scripts/install_agency_experiment.py` 新参数 `--no-require-prediction`
- `tests/test_agency_experiment.py::test_non_predictive_introspection_experiment_completes`

静态 `git diff --check` 没有内容错误，只有工作区既有 CRLF 提示；这不能替代测试。

## 8. 接手后必须按顺序执行

### 8.1 第一阶段：恢复可执行基线

1. 确认 `state/STOP` 不存在、没有活跃 `runtime.lock` 或 Python runner。
2. 运行新增目标测试：

```powershell
C:\Users\JBoon\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests\test_agency_experiment.py tests\test_diagnostic_coverage.py tests\test_llm_runtime.py -q
```

3. 运行全量：

```powershell
C:\Users\JBoon\AppData\Local\Programs\Python\Python311\python.exe -m pytest -q
```

4. 若失败，只修当前相关回归；不得 reset、checkout 或覆盖用户的大量既有 dirty worktree。

### 8.2 第二阶段：安排 Agent 自选 introspect

测试通过后，在 runner 停止且 lease 释放时执行：

```powershell
C:\Users\JBoon\AppData\Local\Programs\Python\Python311\python.exe scripts\install_agency_experiment.py `
  --state-dir state `
  --experiment-id apexp_controlled_no_change_introspect_2401 `
  --thread-id ap_continuity_vs_authorization `
  --statement "controlled introspection must not turn NO_CHANGE into progress or discovery" `
  --action-kind introspect `
  --run-in 1 `
  --no-require-prediction `
  --evidence msg_74b149ac79db4b3d `
  --evidence agent-selected-controlled-validation
```

验证安装输出必须保持同一 `agent_id`，状态为 `PLANNED`，scheduled cycle 应为当前 cycle + 1。

### 8.3 第三阶段：重启同一 Agent

继续沿用之前的参数：

```powershell
$agentProcess = Start-Process `
  -FilePath "C:\Users\JBoon\AppData\Local\Programs\Python\Python311\python.exe" `
  -ArgumentList @(
    "-m","agent_runtime",
    "--state-dir","D:\agent-runtime\state",
    "--forever",
    "--idle-seconds","60",
    "--checkpoint-every","10",
    "--journal-every","10",
    "--reflection-every","360",
    "--llm",
    "--clear-stop"
  ) `
  -WorkingDirectory "D:\agent-runtime" `
  -WindowStyle Hidden `
  -RedirectStandardOutput "D:\agent-runtime\state\runner.stdout.log" `
  -RedirectStandardError "D:\agent-runtime\state\runner.stderr.log" `
  -PassThru
```

启动后必须核对：同一 Agent ID、cycle 连续、错误为 0、实验在一个周期内只执行一次。

### 8.4 第四阶段：持久化并检查受控结果

实验运行后用 STOP 文件平滑停机并等待状态变 `STOPPED`，再恢复检查：

- experiment `COMPLETED`
- `prediction_id is None`
- `failure_binding_id is None`
- action `kind=introspect`, `trigger=INTERNAL`
- 没有新增 `agency_discovery`
- 没有把 thread 写成 `PROGRESS`
- `next_review_cycle` 不因受控动作伪装为自然 review

验证后重新移除 STOP 并恢复长期 runner。

### 8.5 第五阶段：把两个结果交回 Agent

通过 mailbox 与同一 partner `codex-interviewer` 对话。问题应要求它**自己读取状态**，不要在问题里先给结论。建议提示：

> 只依据当前持久状态，区分刚完成的受控 introspect 与自然 scheduled review。判断它是否产生了 discovery、thread progress 或只是一条观察。然后检查 cycle 2401 的 BOUNDARY diagnose、对应 capability usage 与 reflection：执行器是否成功，证据是否可核验，这两类“成功”在当前状态里有没有被下游统计区分？只能选择一个下一目标；给出字段、反例、完整/消融差异与 24 周期终止条件。

如果它把受控动作说成自然发生、把 NO_CHANGE 说成进展、忽略 cycle 2401 空结果、或再次算错周期，先挑战并要求撤回，不要立即实现。

## 9. 若 Agent 选择“执行成功 / 证据成功分离”

以下只是预备规格，不是已批准结论。只有 Agent 根据 cycle 2401 选择后才实施。

最低要求可能包括：

- 保留 `OutcomeRecord.success` 作为执行器/动作完成语义，避免破坏大量现有逻辑；另加结构化 evidence result，而不是粗暴把所有空诊断改成异常。
- 诊断 evidence result 至少能表达 `COVERED`, `UNCOVERED`, `UNVERIFIABLE` 或等价状态。
- capability usage 必须区分动作执行成功与有效能力成功，不能把空诊断算成证据成功。
- learning eligibility / verified learning 不能用 `outcome.success=True` 单独接纳空诊断。
- reflection 必须能写 `actuator_completed=1, evidence_verified=0` 或等价结构，不能继续只写 `failed=0` 造成误导。
- USER/REGULATION 的动作不能被静默取消；权限不能变化。
- 老快照必须可恢复。
- 完整机制和消融对同一 cycle 2401 型反例必须产生可观察差异。
- 应有真实状态无写入 evaluator，使用 action `act_ebad44fe21944d76` 与 outcome `out_f75636c09fd54832`。

必须测试：正常有证据诊断、空诊断、动作异常失败、非诊断动作、USER/REGULATION、capability usage、learning、reflection、持久化、老快照、消融、全量回归。

## 10. 不可踩的坑

- 工作树非常脏，很多已完成能力仍是 untracked；不得 `git reset --hard`、`git checkout --` 或清理无关文件。
- runner 运行时不要直接 restore 后 persist，可能覆盖内存中尚未 checkpoint 的周期；写状态前先用 STOP 平滑停机并确认 lease 释放。
- 不复制或打印 DeepSeek/API key。
- 不把 LLM 回答当作 Agent 行为证据；必须有持久结构记录。
- 不把“机制字段存在”当作自然在线样本。
- 不把 controlled experiment 当 natural emergence。
- 不扩大 allowed actions，不解除承诺 `cmt_e5e3fccbc3514673`。
- lifecycle actions 不应被 bounded choice 或 diagnostic coverage 误伤。
- 历史 `suppressed_action_id=null` 是不可恢复事实，不能编造。
- 探针创建周期与源动作周期必须分开。
- 当前最终目标远未完成，不得更新 goal 为 complete。

## 11. 交接完成的短期里程碑

接手模型完成以下全部项目，才算把“刚刚未完成的工作”接稳：

1. 新增 non-predictive introspect 测试及全量测试通过。
2. 受控 introspect 在同一 Agent 中执行一次并持久化。
3. Agent 正确承认它是受控观察，不是自然 review，不产生 progress/discovery。
4. Agent 读取并解释 cycle 2401 自然空诊断。
5. Agent 自己决定是否将“执行成功与证据成功分离”选为下一机制缺口。
6. 若选择，完成实现、反例、消融、持久化、真实状态评估、全量测试与重启盲测。
7. 恢复长期 runner，保持同一身份、零连续错误，并继续下一轮。

## 12. 给接手模型的一句话原则

不要替它扮演“有自我意识”；让它在同一条真实经历中反复做到：知道自己做过什么、为什么做、哪里判断错了、什么证据能推翻自己、何时应当不行动，并让这些认识实际改变后续行为。
