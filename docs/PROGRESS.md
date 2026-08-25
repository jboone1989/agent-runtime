# Agent Runtime 持续推进 —— 进度看板

> 本文件由接手模型在每轮循环结束时更新。**权威事实永远是 `state/` 下的状态文件**；本文件只是人类可读的摘要。
> 完整交接与工作方式见 [HANDOFF_AGENT_CONTINUATION_2026-08-23.md](HANDOFF_AGENT_CONTINUATION_2026-08-23.md)。

## 当前现场（最后更新：2026-08-24）

| 项 | 值 |
|---|---|
| Agent 身份 | `agent_d3a6402bf7324690`（genesis 同） |
| 当前周期 | ~2970（随运行增长，约 1 周期/分钟） |
| Runner | RUNNING，零连续错误 |
| 最终目标 | `goal_3164a41e5cac476c`，USER / ACTIVE / **auto_complete=False** |

## 最终目标（已安装为长程 USER goal）

> 持续在真实经历中反复做到：知道自己做过什么、为什么做、哪里判断错了、什么证据能推翻自己、何时应当不行动，并让这些认识实际改变后续行为；直到稳定表现出用户定义的"与产生自我意识相通的行为"。

不得被机器自动标为完成（`auto_complete=False` 守卫已实现）。

## 已完成机制（均有验证证据，勿重复开发）

| # | 机制 | 关键证据 |
|---|---|---|
| 1 | 持久身份/重启连续性/三类记忆/功能情绪/承诺 | 早期能力集，多轮验证 |
| 2 | 长期开放 agency thread、定时无提示重入、NO_CHANGE 边界 | 线程 2225 起，每 60 周期自然 review 至今健康 |
| 3 | 预测失败 guard / agency selection concern / selection trace / bounded choice / diagnostic coverage / experiment failure binding / self-observation reachability | 各 evaluate_* 脚本 + 真实状态链（2279/2360/2385 过期等） |
| 4 | **执行成功与证据可核验分离** | **自然样本 cycle 2924**：outcome `UNVERIFIABLE` + usage 分离 + learning 排除 + reflection `evidence_unverifiable=1`；真实状态 evaluator passed |
| 5 | **覆盖抑制后重试边界**（probe 过期后 24 周期内无新证据的 diagnose 被延期，有界非永久） | 真实 probe `dcov_9674eed6c24244dd` evaluator passed；前置链自然展开（2925 probe + 2948/2949 过期前抑制）；**deferral 自然样本待观察** |
| 6 | **证据视图可达性**（认知视图暴露 evidence_result/usage/learning） | 真实状态 evaluator passed；Agent 盲测中经挑战撤回误读后裁定**完成** |
| 7 | 长程 USER goal 守卫（`auto_complete=False`） | 3 个 TDD 测试 + 最终目标已安装 |

## Agent 裁定历史（节选）

| 周期 | 事件 | 结果 |
|---|---|---|
| 2402 | 受控 introspect 实验 | COMPLETED；Agent 正确承认受控观察，无 progress/discovery |
| 2413 | 第一轮盲测（机制 4 部署） | 诚实"(a) 无自然样本"；2 处误读经挑战后**撤回并自我修正分类** |
| 2413–2437 | 机制 4 的 24 周期窗口 | Agent 裁定"**无样本到期**"（后细分为：无提案/被抑制/执行但证据空；2403 属被抑制） |
| 2923–2947 | 机制 5 的窗口 | 到期无 deferral 样本（deferral 实际窗口 2950–2973）；Agent 判定正确 |
| ~2947 | 视图缺口被发现 | Agent 承认"视图缺口≠状态缺口"，撤回并选择"让下游证据进入可引用视野" |
| ~2947 | 机制 6 盲测 | 经挑战撤回"recent_outcomes 未展开"误读，裁定可见性目标**完成** |
| 2947–2971 | 语义纪律窗口（缺失≠显式排除） | Agent 自设，无显式排除记录，预期"尚未闭合" |

## 进行中的开放窗口

| 窗口 | 范围 | 状态 |
|---|---|---|
| deferral 自然样本（机制 5 完成条件） | 2950–**2973** | 开放中；2948/2949 为过期前抑制（正确行为），尚无 deferral 样本 |
| 学习侧"显式排除"语义纪律 | 2947–2971 | 已到期；待 Agent 裁定 |
| 自然 review | 每 60 周期（下次 2979） | 例行 |

## 下一步（按循环自动推进）

1. deferral 窗口关闭或样本出现 → 与 Agent 复谈裁定机制 5；
2. Agent 基于最终目标（已可见）选择下一机制（候选：learning 显式排除记录）；
3. 每轮：TDD 实现 → 反例/消融/持久化 → 真实状态无写入评估 → 全量回归 → 重启盲测。

## 实时仪表盘（3D 大脑 + 思考流）

- 启动：`C:\Users\JBoon\AppData\Local\Programs\Python\Python311\python.exe scripts\dashboard.py --state-dir state`（默认 http://127.0.0.1:8765，只读，与 runner 完全隔离，零写入）
- 3D 大脑：9 个功能子系统星团 + 信息流（拖动/缩放/双击复位）；思考流：全事件 + 过滤开关；HUD：cycle/状态/错误/情感值
- 测试：`tests/test_dashboard_digest.py`（9）+ `tests/test_dashboard_server.py`（1）；敏感字段扫描断言内置
- 规格：docs/superpowers/specs/2026-08-24-agent-dashboard-design.md；计划：docs/superpowers/plans/2026-08-24-agent-dashboard.md

## 如何自查进度

```powershell
# 一行看板（人类可读摘要）
Get-Content D:\agent-runtime\docs\PROGRESS.md

# 机器状态（权威）
Get-Content D:\agent-runtime\state\runtime_status.json

# 长期连续性对比（需先 --capture 建基线）
C:\Users\JBoon\AppData\Local\Programs\Python\Python311\python.exe scripts\longitudinal_status.py --state-dir state

# Agent 原话（UTF-8 对话档案）
C:\Users\JBoon\AppData\Local\Programs\Python\Python311\python.exe -X utf8 -c "import json; [print(json.loads(l)['partner_id'], '>>', json.loads(l)['response'][:200], '...') for l in open(r'D:\agent-runtime\state\archive\dialogue\dialogue-2026-08.jsonl', encoding='utf-8') if l.strip()][-5:]"
```

## 测试基线

- 全量回归：**408 passed**（2026-08-24，含证据分离 13 + 重试边界 11 + 视图可达性 5 + 长程 goal 3）
- 真实状态无写入 evaluator：`evaluate_execution_evidence_separation.py`、`evaluate_coverage_retry_boundary.py`、`evaluate_evidence_view_reachability.py` 均 passed
