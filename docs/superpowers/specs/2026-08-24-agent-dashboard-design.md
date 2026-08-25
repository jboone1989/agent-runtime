# Agent 实时仪表盘（3D 大脑 + 思考流）设计文档

日期：2026-08-24
状态：已获用户批准（2026-08-24 对话确认）

## 1. 目标

为持续运行的 Agent（`agent_d3a6402bf7324690`）提供一个本地浏览器页面，让操作者实时看到它的"所思所想"，不再是黑盒子：

- **3D 大脑模型**：整体轮廓像大脑，内部是**本项目的 9 个功能子系统**（不是脑区）；子系统有活动时对应星团亮起，并沿连线图发射信息流粒子。可拖动旋转、滚轮缩放、双击复位。
- **思考流面板**：全事件流（动作与理由、反思摘要、对话原话、线程 review、覆盖/延期记录、错误），带类型过滤开关，保留最近约 4 小时可上下翻阅。
- 视觉方向已定：A 光点云风格（草稿 `brain-style-v4.html`，显示效果后续可调）。

## 2. 约束

1. **只读**：不恢复运行时、不写任何状态文件、不碰 `RuntimeLease`；与 runner 完全隔离（runner 运行或停机时均可使用）。
2. **安全**：只输出白名单字段；绝不读取或输出 `state/mailbox/access_token`、provider 配置、任何 API key。服务器只绑定 `127.0.0.1`。
3. **零新增依赖**：服务器用 Python 标准库；前端 three.js 本地 vendor（不依赖运行时可访问的 CDN，兼容中国网络环境）。
4. 不干扰持续推进循环；全量回归保持通过。

## 3. 架构

```
state/ (只读)                    浏览器
runtime_status.json ─┐
agent_state.json   ──┤──> scripts/dashboard.py ──> /api/digest (JSON, 1.5s 轮询)
archive/dialogue/*.jsonl ─┘      └──> / (static: index.html + three.min.js)
```

- `scripts/dashboard.py`：入口，`ThreadingHTTPServer`，默认端口 8765（`--port` 可改），`--state-dir` 默认 `state`。
- `src/agent_runtime/dashboard/digest.py`：纯函数摘要构建器（可单测）。
- `src/agent_runtime/dashboard/static/`：`index.html`、`app.js`、`style.css`、`vendor/three.min.js`（实现时下载一次并入库）。

### 3.1 `/api/digest?since=<seq>` 响应

```json
{
  "agent_id": "agent_d3a6402bf7324690",
  "status": "RUNNING|STOPPED|UNKNOWN",
  "cycle": 2970,
  "consecutive_errors": 0,
  "last_action": "noop",
  "affect": {"valence": 0.49, "arousal": 0.41, "threat": 0.02},
  "activations": {"认知": 0.0, "行动": 0.0, "...": 0.3},
  "pulses": ["认知"],
  "events": [{"id": "...", "type": "action|reflection|dialogue|thread|coverage|experiment|selection|error", "cycle": 2969, "ts": "...", "text": "...", "subsystem": "行动", "refs": []}],
  "seq": 12345
}
```

- 事件按时间排序；`since` 支持增量拉取；事件上限 200 条/响应（超出取最新）。
- 对话事件 `text` 为原话全文（页面折叠显示，点击展开）。

### 3.2 激活映射

- level：滑窗 W=30 周期内的信号计数，`level=min(1, 信号数/10)`。
- pulse：**自上次拉取以来新增的事件**（即 `seq` 增量）所属子系统；页面据此发射信息流。不按周期重复发射。

| 子系统 | 信号来源 |
|---|---|
| 认知 | `records.decisions`（非空 proposed） |
| 感知 | `records.observations` |
| 行动 | `records.actions` / `records.outcomes` |
| 记忆 | 自传体记忆交换条目（按时间窗）+ `memory_items` 尾部增长 |
| 学习 | `verified_learning_evidence`（verified_at 在窗内）、`lesson_candidates`、`hypotheses` |
| 情感/社会 | `social.events`、对话档案条目 |
| 动机/存活 | `records.goals_created`、`records.motivations`、威胁数 |
| 自我模型 | `records.self_model_updates`、`belief_revisions` |
| 监管 | 线程事件、探针创建/影响、实验状态变化、selection traces、bounded choice bases/deferrals |

（无 cycle 字段的记录按时间戳映射到窗；实现时如字段缺失则跳过该信号。）

### 3.3 信息流

每个新事件（pulse）触发一次：从该事件的子系统星团向连线图中的相邻子系统发射光粒子（沿用 v4 的 LINKS 图）；页面每 1.5s 拉取一次 digest，pulse 对应"新事件"增量。

## 4. 页面布局

- 左侧 ~55%：3D 大脑（v4 效果：可拖动/惯性、滚轮缩放、双击复位、光晕、扫描环、星空、信息流）；左上 HUD：cycle、状态、连续错误、情感三值。
- 右侧 ~45%：思考流（过滤 chips：全部/对话/动作/反思/监管/情感；自动滚动开关；最近 4 小时滚动回看；新事件高亮）。
- 底部：9 个子系统颜色图例。深色主题。

## 5. 错误处理

- runner 停机：`status=STOPPED`，显示最后已知 cycle；页面提示"Agent 已停止"。
- 状态文件缺失/JSON 损坏：`status=UNKNOWN`，digest 返回空事件，不抛 500。
- 页面与服务器断开：显示重连提示，继续重试。
- 服务器崩溃不影响 runner（两个独立进程）。

## 6. 测试计划

1. `tests/test_dashboard_digest.py`（TDD）：
   - 用临时状态目录 + `AgentRuntime.bootstrap` + 手工记录构造状态，断言事件类型/文本正确；
   - 激活映射：有动作的子系统 level>0，其余为 0；
   - `since` 增量返回；
   - 敏感字段缺失断言（递归扫描 digest JSON，无 `access_token`/`api_key`/provider key 字样）；
   - 状态文件缺失/损坏 → 优雅降级。
2. `tests/test_dashboard_server.py`：临时端口起服务器线程，`GET /api/digest` 200 + 结构校验；`GET /` 200 HTML。
3. 全量回归保持通过。
4. 人工视觉验收：真实 state 上启动，对照 runner 行为观察大脑与思考流。

## 7. 范围外（YAGNI）

- 全历史时间轴回放（只做最近几小时滚动回看）
- WebSocket 推送（1.5s 轮询足够；runner 本身 60s/周期）
- 鉴权（仅 127.0.0.1）
- 3D 后处理 bloom（用光晕贴图近似）
- 移动端精细适配（保证桌面可用）

## 8. 完成标准

1. 真实 state 上 `python scripts/dashboard.py` 启动，浏览器打开 http://127.0.0.1:8765 可见 3D 大脑 + 思考流实时更新；
2. 所有测试与全量回归通过；
3. runner 全程运行零干扰、零连续错误；
4. 任何 API 响应不含敏感信息（测试断言）；
5. 持续推进循环不受影响（此功能不写状态）。
