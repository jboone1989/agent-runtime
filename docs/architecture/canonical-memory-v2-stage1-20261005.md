# Canonical Memory v2：第一阶段实施记录

日期：2026-10-05（北京时间）

本阶段实现唯一 canonical 底座的不可改写修订、采纳决定和双时间读取，保留 SQLite 数据及现有调用链。Graphiti/Neo4j、PostgreSQL、pgvector、主题层次和检索控制迁移均不属于本次交付。

## 已核实的生产基线

- Runtime：`3cf119c92ab45c62be59792f7d1d62edcd641933`
- Ferro：`1a41cd97909c60d0c737d7499d6427d038edd7c1`
- 两个生产 release 工作树均干净。
- 服务主进程从 Ferro 正式 release 启动；使用同一解释器、环境及 .pth 文件的新进程，确认 Memory 模块来自上述两份 release。此检查不声称读到了运行中 Python 的模块缓存。
- Ferro 生产提交原先尚未进入 main；本阶段已将 main 快进到该提交，保留原有未提交的浏览器/worker 修改。它们不属于 Memory 提交。

旧 Stage 0 的脏生产 release 结论属于当时状态，已被本次现场检查更新。

## 数据与接口

`memory_revisions` 保存完整候选快照、稳定 revision/claim ID、修订序号、正文、依据、生成版本、范围、recorded_at 和世界有效区间。数据库触发器禁止更新与删除。

`memory_adoptions` 保存每次接受、争议、撤回等决定的理由、依据和记录时间，也不可更新和删除。候选的 ACTIVE 字段本身不使它成为采用事实；读取必须有采纳记录。

`memories` 保留为最新采用状态的兼容视图；它与新记录在同一个数据库事务更新，不成为另一份权威。

公共入口：

- `propose_revision(record) -> revision_id`：保存候选，不改变当前事实。
- `record_adoption(decision) -> revision_id`：记录采用决定并更新兼容视图。
- `read_claim(id, valid_at=..., known_at=..., scope...) -> record | None`
- `query_claims(..., limit=...) -> records`
- `revision_history(id, scope..., limit=...) -> snapshots + decisions`

`valid_at` 查询世界有效时间；`known_at` 限制当时已经记录且已经采用的内容。UTC、时区偏移和微秒统一比较；有效区间为 [from, to)，未知边界保留为空。时间过滤在采用候选选择时执行，状态过滤在选出相应最新决定后执行，避免把被撤回的旧 ACTIVE 版本重新召回。

租户/所有者过滤在 SQL 中、排序和 limit 之前执行；subject 与 access_scope 可进一步限定。同一 claim 不允许换范围。旧库没有租户和所有者 ID，标记为 `legacy` / `LEGACY_UNKNOWN`，不假装知道真实身份。部署服务必须配置并传递授权范围；本阶段不补造旧数据的租户身份。

新 API 不截短正文、不隐式限制调用者的结果 limit；错误数值和无效引用明确失败。现有 v1 append API 的既有规范化行为保留以维持原调用兼容，后续消费者切换到 v2 时移除旧入口。

旧的 append/transition 自动生成修订与采纳记录，避免现有 Formation/学习调用继续只改当前行。状态变化在持有写锁后读取当前记录，避免并发覆盖。兼容 derived-view hook 仍在同一事务内；hook 失败会回滚整个变更。

## 迁移边界

保留 v1 schema_meta/schema_version 和原始行，另用 `canonical_contract_version=2` 标记扩展。首次初始化逐行建立旧库当前状态快照及对应采纳记录，迁移完成标记与记录一并提交。以后重开不再全量重建修订。

旧 v1 没有保存完整历史正文快照。旧事件日志原样保留；新历史入口以迁移时间作为完整快照历史的可验证起点，返回 `history_origin=LEGACY_SNAPSHOT` 和 `history_available_from`。更早的 known_at 不会伪造历史答案。

这是 claim/revision 层的双时间契约。原文归档的历史选版、Episode/Procedure 的时间读取和所有 retrieval provider 的时间过滤仍需在后续迁移实现；不能称整个 Memory 的历史检索已经完工。

## 验证

- Runtime canonical/formation/episode/semantic/retrieval：50 项通过。
- Runtime ChatGPT/browser/wakeful：72 项通过。
- Ferro 兼容：37 项通过，1 项现有失败。
- Ferro 生产内容上下文相关回归：12 项通过。

Ferro 的 `test_owner_429_is_bounded_and_duplicate_wake_does_not_retry_api` 失败于旧 Public Chat 429 后 follow-up 调度断言。在未改动的生产 Runtime/Ferro 组合上复现同样失败，记为原有问题，本次没有修改该行为或测试。

真实只读快照验证：

- 2,091 条 canonical 记忆迁移为 2,091 条初始修订及采纳。
- memories、memory_events、entities、topics 的数量与逐行哈希迁移前后完全一致。
- 334 个原文归档文件已复制备份并校验哈希。
- SQLite integrity_check：ok。
- 迁移 1.669 秒，重开 0.027 秒。
- 整个验证进程峰值 RSS 63,072 KiB（约 61.6 MiB），包含导入探测、数据库拷贝、归档备份及校验；不是完整内容服务峰值。
- 详细收据：`/home/ubuntu/validation/canonical-v2-20261005-final/receipt.json`
- 原始数据库与原文备份：`/home/ubuntu/validation/canonical-v2-20261005-final/backup/application/memory_system`
- 摘要：`canonical-v2-snapshot-baseline-20261005.json`

## 发布与后续顺序

Runtime 改动先进入 main，再从精确 commit 创建干净 release；Ferro 继续使用已核实的 1a41cd9。正式切换时统一重启消费进程，避免旧适配器和新适配器同时写 canonical。旧二进制仍能读 v1 兼容行，但不能作为 v2 历史写入者继续运行。

不以“恢复旧备份”作为自动回退动作：上线后新 receipt/event 必须保留。若上线后发生兼容错误，应暂停写入并修复；恢复数据必须另行核对增量记录。

随后按冻结方案继续：

1. 把可恢复检索控制从 Ferro 移入 Runtime。
2. 使历史原文读取、Episode/Procedure 和 retrieval filters 使用同一 canonical 时间与范围契约。
3. 测量数据库/模型预算后接入 Graphiti + Neo4j，并验证 canonical 采用边界。
4. 逐个切换层次、向量和全文 provider，通过验收后删除对应旧实现。

整体新 Memory 架构尚未完成；本阶段也不以代码测试代替发帖 POSTED receipt 或学习行为改变的外部验收。


## 正式生产切换结果

北京时间 2026-10-05 01:19:55 恢复 content-agent.service。

生产精确组合：

- Runtime：`fb8bbd3b4665e7ab2404245910916c4627ce7418`
- Ferro：`1a41cd97909c60d0c737d7499d6427d038edd7c1`

两个版本均已进入并推送 main；发布从干净 commit 建立独立 release。现有 activation 脚本将部署收据写入 release 顶层，造成一个 untracked 元数据文件；本次已将该收据保留在 validation 目录，复查两个 release 均干净。没有把后续源码手工补入 release。

停止写入后，重新备份最新数据库及原文再进行实际迁移：

- 迁移 2,091 条原有记忆，生成同数初始修订和采纳记录。
- 旧 memories、events、entities、topics 逐行哈希一致，integrity_check 为 ok。
- 实际迁移耗时 0.732 秒，进程峰值 RSS 61,840 KiB（约 60.4 MiB）。
- 迁移备份及原文：`/home/ubuntu/validation/canonical-v2-production-20261005/application/memory_system`
- 迁移、部署、在线验证收据均在 `/home/ubuntu/validation/canonical-v2-production-20261005`。

恢复后真实业务新增 2 条 canonical 记忆，写入 4 条修订及同数采纳记录（包括旧记录重新归档的兼容调用）。此数量证明调用链确实使用 v2；不能把重复归档认定为新知识或学习进步。在线验证时为 2,093 条 memories、2,095 条 revisions 和 2,095 条 adoptions。

实际历史读取验证：同一旧 claim 的历史与当前读取指向不同快照；上线后新建 claim 在迁移时的 known_at 查询不可见，在当前查询可见。服务和六个独立 worker 正常 active；新进程导入探测确认 Runtime/Ferro 均来自指定 release。

内容服务采样约 492.5 MiB，启动后峰值约 774.8 MiB，仍使用约 138.9 MiB swap。此次迁移的约 60 MiB 开销不代表整个系统资源问题已经解决，也不能由此推断 Neo4j 与模型可共同放入 VM09。

新记录底座已经上线；Graphiti、层次索引、检索控制迁移、结果驱动学习和发帖闭环仍须继续验收。
