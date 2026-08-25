# v0.3.0 Evaluation Foundation — RFC

日期：2026-08-25
状态：设计待确认（未开始实现）

## 1. 目标

让 Agent Runtime 首次可靠地区分：

> **"发生了一个结果"** vs **"这个结果真的实现了目标"**

这是 v0.3 Learning / Memory Governance / v0.4 Evolution 的地基。

本轮只做 v0.3.0：Evaluation Foundation。不做 Hypothesis 晋升、Credit Contribution、Strategy 生命周期、Memory Governance、Evolution（见 §10）。

## 2. 现状盘点（只读探索结论）

### 2.1 已有、直接可复用的组件

| 组件 | 位置 | 复用方式 |
|---|---|---|
| 诊断证据分类器（COVERED/UNCOVERED/UNVERIFIABLE + missing units） | `src/agent_runtime/action/evidence.py` | DeterministicVerifier 的 outcome 证据判定核心 |
| 覆盖引擎的 CoverageResult | `src/agent_runtime/agency/coverage.py` | 与上面同源（已共享分类函数） |
| Acquisition 验证（VerificationProbe/VerificationResult：probe kind + expected digest + reasons + evidence） | `src/agent_runtime/capabilities/acquisition/models.py` | 是 acquisition 领域的验证记录；**不动它**，新评估层通过 `CAPABILITY_RESULT` 目标类型包装/引用，不重复造轮子 |
| VerifiedLearningEvidence（含 UNVERIFIABLE/UNCOVERED 学习门槛） | `src/agent_runtime/learning/evidence.py` | 收据成为 verified success 的机器判据来源 |
| PredictionFailureAdapter（MATCHED/VIOLATED 判定） | `src/agent_runtime/self_model/failure_adaptation.py` | 预测类 verifier 可引用 |
| ExperienceTrace（Goal→Plan→Step→Decision→Action→Outcome） | `src/agent_runtime/experience/trace.py` | v0.3.1 加 `verification_refs` 扩展（本轮只加可选字段，见 §8） |
| LearningController（credit/evidence/strategy registry 治理） | `src/agent_runtime/learning/controller.py` | 本轮只改 `_verify` 门槛：verified success 必须有 PASS 收据 |
| MemoryGovernor（quarantine/forget/consolidate 已存在） | `src/agent_runtime/memory/governance.py` | v0.3.5 再用，本轮不动 |
| 持久化 codec（dataclass 信封、缺省字段向后兼容） | `src/agent_runtime/codec.py` | 新记录直接可持久化 |
| Contract v1 outcome_view 的 `extensions` 槽 | `contracts/v1/schemas/outcome_view.schema.json` | 若需跨协议扩展时使用；本轮不修改任何 frozen schema |
| Evolution 包（AgentVersion/Proposal/Activation/Evaluation/Rollback） | `src/agent_runtime/evolution/` | v0.4 再动，本轮不碰 |

### 2.2 缺口（本层必须新建的）

- 没有通用 VerificationReceipt / Verifier Protocol / VerificationPolicy。
- 没有目标级（Goal/Plan/Step）验证——只有 outcome 级证据分类与 acquisition 探针。
- 学习门槛只查 outcome 自身字段，没有"必须经过验证"的机器规则。
- ResourceBudget 只有 compute/financial，没有认知预算（v0.3.6 再做）。

## 3. 架构

新增包：

```
src/agent_runtime/evaluation/
    __init__.py
    model.py        # 状态/目标类型/VerificationTarget/VerificationReceipt
    verifier.py     # Verifier Protocol
    deterministic.py# DeterministicVerifier / SchemaVerifier / ExecutionVerifier / GoalVerifier
    llm.py          # LLMVerifier（v0.3.0 仅实现类与策略挂点，默认策略不启用）
    composite.py    # CompositeVerifier（ALL_REQUIRED/ANY_SUFFICIENT/WEIGHTED/QUORUM/PRIORITY）
    registry.py     # VerifierRegistry + ReceiptLog（receipts_by_target / latest_for_target）
    policy.py       # VerificationPolicy（LOW/MEDIUM/HIGH/CRITICAL 分级与 verifier 要求）
```

数据流（在现有 `runtime.step` 内接入，位置在 capability usage 记录与 `learning.observe` 之间）：

```
cycle 内 outcomes / completed goals / completed plans
        ↓
VerificationPolicy.plan(targets)        # 按风险分级决定验证哪些、用什么 verifier
        ↓
VerifierRegistry.verify(target, context) # 每目标产生 VerificationReceipt（含复合）
        ↓
ReceiptLog（持久化 agent_state.evaluation_receipts）
        ↓
learning.observe(traces, outcomes, receipts)   # verified success = 至少一条 PASS 收据
```

## 4. 数据模型

### 4.1 VerificationStatus

```python
class VerificationStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNCERTAIN = "UNCERTAIN"
    ERROR = "ERROR"
    NOT_APPLICABLE = "NOT_APPLICABLE"
```

### 4.2 VerificationTargetType

```python
class VerificationTargetType(str, Enum):
    OUTCOME = "OUTCOME"
    ACTION = "ACTION"
    PLAN_STEP = "PLAN_STEP"
    PLAN = "PLAN"
    GOAL = "GOAL"
    ARTIFACT = "ARTIFACT"
    CAPABILITY_RESULT = "CAPABILITY_RESULT"
    DELEGATION_RECEIPT = "DELEGATION_RECEIPT"
```

### 4.3 VerificationTarget

```python
@dataclass(frozen=True)
class VerificationTarget:
    target_type: VerificationTargetType
    target_ref: str              # outcome_id / action_id / goal_id / plan_id / ...
    payload: dict[str, Any] | None = None   # 可选；缺省时由 runtime 按 ref 解析
```

### 4.4 VerificationReceipt

单一收据类型（复合验证用 `sub_receipts` + `details` 表达，不建第二套记录类型）：

```python
@dataclass
class VerificationReceipt:
    receipt_id: str
    agent_id: str
    verifier_id: str
    verifier_version: str
    target_type: VerificationTargetType
    target_ref: str
    status: VerificationStatus
    score: float | None          # 与 status 独立；score=0.75 + status=UNCERTAIN 合法
    confidence: float
    evidence_refs: list[str]
    counterexample_refs: list[str]
    observed_at: datetime
    verified_at: datetime
    cost: float = 0.0
    latency_ms: int = 0
    method: str = "deterministic"
    notes: str = ""
    sub_receipts: list[str] = field(default_factory=list)  # 复合验证的子收据 id
    details: dict[str, Any] = field(default_factory=dict)   # 复合策略与逐项结果
```

### 4.5 VerifierContext

```python
@dataclass
class VerifierContext:
    cycle: int
    timestamp: datetime
    risk_level: VerificationRiskLevel      # policy 判定
    state_dir: Path | None = None          # 只读状态引用，verifier 自行只读
```

### 4.6 VerificationPolicy / VerificationRiskLevel

```python
class VerificationRiskLevel(str, Enum):
    LOW = "LOW"; MEDIUM = "MEDIUM"; HIGH = "HIGH"; CRITICAL = "CRITICAL"

@dataclass
class VerificationPolicy:
    # 每级要求: verifier ids, 最低 confidence, 最低 score, 最大 cost
    def plan(self, target, context) -> list[str]   # 该目标应运行的 verifier id 列表
    def risk(self, target, action_kind, goal) -> VerificationRiskLevel
```

风险分级规则（确定性 v1，可被 policy 参数覆盖）：

| 级别 | 依据（默认规则） |
|---|---|
| LOW | noop / introspect / record / observe（无外部影响） |
| MEDIUM | diagnose / maintain / conserve / contain / probe_provider |
| HIGH | switch_provider / restore_config / disable_capability / 网络委托 |
| CRITICAL | 产生外部副作用或涉及授权变化（当前无动作默认命中；预留） |

级别影响：LOW 至少 `deterministic:outcome`；MEDIUM 加 `schema` + `execution`；HIGH 加 `composite(all_required: deterministic+schema+execution)`，最低 confidence/score 提高；CRITICAL 额外要求 operator 或 LLM verifier（v0.3.0 中 CRITICAL 默认返回 UNCERTAIN 并记录"需要操作者确认"，不自动 PASS）。

## 5. Verifier 实现（第一版）

```python
class Verifier(Protocol):
    verifier_id: str
    verifier_version: str
    def can_verify(self, target: VerificationTarget, context: VerifierContext) -> bool: ...
    def verify(self, target: VerificationTarget, context: VerifierContext) -> VerificationReceipt: ...
```

| Verifier | 逻辑（确定性） |
|---|---|
| `DeterministicVerifier`（id=`deterministic`，outcome/action 级） | success=False 且无证据 → FAIL；success=True 且 evidence_result=COVERED → PASS(score 按证据完整度)；success=True 且 UNVERIFIABLE → **UNCERTAIN**(score≤0.5)；UNCOVERED → UNCERTAIN；非诊断动作（evidence_result=None）按 success + effects 结构判定 PASS/FAIL |
| `SchemaVerifier`（id=`schema`） | 目标记录必填字段完整、类型合法（用现有 dataclass 规则校验 payload） |
| `ExecutionVerifier`（id=`execution`） | 动作执行异常与否：effects 含 error → FAIL；正常返回 → PASS（执行层，不评价证据） |
| `GoalVerifier`（id=`goal`，goal/plan/step 级） | 层级聚合：GOAL：goal.status=COMPLETED 且其 plan 的 PASS 收据链存在 → PASS；status=FAILED → FAIL；其他 → UNCERTAIN。PLAN/STEP 同理聚合子收据 |
| `LLMVerifier`（id=`llm`） | 类与协议挂点先建；默认 policy 不启用；输出一律视为候选证据（details 标注 llm） |
| `CompositeVerifier` | 策略 ALL_REQUIRED / ANY_SUFFICIENT / WEIGHTED / QUORUM / PRIORITY；**单元测试 FAIL 不能被 LLM PASS 覆盖**：FAIL 子项在 ALL_REQUIRED/QUORUM 中直接判 FAIL |

## 6. 与 Learning 的衔接（最小改动）

- `LearningController._verify` 增加机器规则：**verified success 要求该 outcome 至少一条 `status=PASS` 的收据**（调用方传入 `receipts` 映射；不传则保持旧行为，保证既有单测兼容）。
- 效果：`action success + goal verification FAIL` 不会进入 verified learning——正是 DoD #1/#7。
- v0.3.1 再加 `ExperienceTrace.verification_refs` 与 VerifiedExperience View；本轮只预留字段位置（见 §8）。

## 7. Runtime 接入点

`runtime.py` 中（现有 `learning.observe` 调用之前）：

```python
targets = _verification_targets(action_records, outcomes, self.goals, plan)
receipts = self.evaluation_registry.verify_all(targets, policy=self.evaluation_policy, context=...)
self.evaluation_receipts.extend(receipts)
# learning.observe(traces, outcomes, receipts=receipts_by_outcome)
```

- 每周期对**本周期新产生的** outcome 和**状态变为终态的** goal/plan 做验证（增量，不重验历史）。
- 纯确定性路径，默认零 LLM 消耗；policy 可后续开启 LLMVerifier。
- USER/REGULATION 触发与生命周期动作不豁免验证（验证≠抑制，只影响学习与记录）。

## 8. ExperienceTrace 扩展

- 本轮：给 `ExperienceTrace` 增加可选字段 `verification_refs: list[str] = field(default_factory=list)`（dataclass 缺省，旧快照兼容；trace 不在 frozen contract schema 内，属内部记录扩展）。
- 本轮**不**填充该字段；v0.3.1 填充并形成 VerifiedExperience View。

## 9. 文件清单与影响

**新增**
- `src/agent_runtime/evaluation/{__init__,model,verifier,deterministic,llm,composite,registry,policy}.py`
- `tests/test_evaluation_foundation.py`（+ persistence/compat 用例）

**修改**
- `src/agent_runtime/runtime.py`：registry/policy 初始化、cycle 内验证接入、持久化字段 `evaluation_receipts`
- `src/agent_runtime/learning/controller.py`：`observe(..., receipts: dict | None = None)` 门槛
- `src/agent_runtime/experience/trace.py`：`verification_refs` 可选字段
- `docs/PROGRESS.md`：记录 v0.3.0

**不修改**：contracts/v1 任何 schema、acquisition、memory、evolution、constitution/autonomy。

**兼容性**：全部为新增可选字段与新记录列表；旧快照 restore 走缺省值；不传 receipts 时学习门槛保持旧行为。

**持久化**：`agent_state.json` 新增 `evaluation_receipts`（收据列表，带 codec 信封）；restart 恢复；`old snapshot → new runtime` 与 `new snapshot → restart` 双向测试。

## 10. 本轮明确不做

Hypothesis/status/scope 扩展、CreditAssignment contribution/role 扩展、Strategy 生命周期、Memory Governance（provenance/contradiction/decay/protection）、Consolidation 接线、LearningPipeline/Fast-Slow loop、CognitiveBudget、Long-horizon 评测、Evolution 全部、fine-tuning、`verification_refs` 填充（v0.3.1）。

## 11. 验收测试（v0.3.0 DoD，全部纳入 pytest）

1. `test_action_success_but_goal_verification_fails`（action PASS、goal FAIL → 不产生 verified learning）
2. `test_unverified_outcome_is_not_marked_verified`
3. `test_composite_verifier_respects_required_failure`（ALL_REQUIRED 中任一 FAIL → 整体 FAIL；LLM 子项不能覆盖）
4. `test_uncertain_verification_is_preserved`（score≠status 合法；UNCERTAIN 不升级为 PASS）
5. `test_verification_receipt_has_evidence`
6. `test_verification_persists_after_restart`
7. `test_old_snapshot_restores_without_verification_state`
8. `test_high_risk_goal_requires_stronger_verification`
9. `test_verifier_error_does_not_become_pass`（verifier 抛异常 → status=ERROR，绝不 PASS）
10. `test_same_target_can_have_multiple_verification_receipts`

外加：全量回归不破坏（当前基线 421 passed）、收据线性追溯查询（receipts_by_target / latest_for_target）、策略分级单测。

## 12. 待确认的设计取舍

1. 单一 `VerificationReceipt` 类型承载复合结果（`sub_receipts`+`details`），不建独立 `CompositeVerificationReceipt`——保持一套持久化与查询路径。
2. 风险分级默认规则按动作种类（§4.6 表），策略参数可覆盖。
3. CRITICAL 级 v0.3.0 默认判 UNCERTAIN 并记录"需操作者确认"，不自动放行。
4. `deterministic` verifier 直接复用 `action/evidence.py` 分类器（单一事实源）。
