# Capability Fabric — Implementation Rules

> 放置于三个仓库的架构规划中。基准设计：设计.txt（v0.2.2 起）。
> Runtime 版本：v0.2.2 Capability Foundation。

## 目标

让数字主体具备发现、获得、验证、使用和积累外部能力的能力，
而不是默认自己重新实现所有功能。

## 架构边界

1. VirtualBrain 研究主体如何感知能力边界、形成探索/求助/学习动机。
2. Agent Runtime 负责 Capability 的 discovery、resolution、acquisition、
   verification、activation、usage 和 persistence。
3. Agent Network 负责远程 Agent capability discovery、Evidence、
   verification history 和 trust projection。
4. 不创建第四个 Capability Fabric 产品，除非未来出现两个以上
   与 Runtime 无关的独立 consumer，需要抽离中立协议。

## 协议规则

5. Contract v1 root shape 已冻结，不得为 Capability 修改 Contract v1。
6. Agent Skills 使用现有 Agent Skills specification。
7. MCP 使用官方 MCP Registry。
8. Remote Agent capability 优先兼容 A2A AgentSkill。
9. 不创建新的通用 Skill 标准、MCP Registry 或 Agent capability protocol，
   除非现有标准确实无法表达需求。

## Capability 决策顺序

10. REUSE_LOCAL
11. DELEGATE_REMOTE
12. ACQUIRE
13. ADAPT
14. CREATE

15. CREATE 是最后选项。
16. 创造 reusable capability 前必须有 CapabilityDecision 和搜索证据。
17. GitHub stars 不得直接作为 trust 或 quality。
18. Network trust 不等于 Runtime authorization。

## 安全

19. 不自动执行任意 GitHub repository。
20. 不自动执行未经检查的 Skill scripts。
21. Executable capability 必须经历：
    INSPECT → POLICY → SANDBOX → VERIFY → ACTIVATE。
22. Capability 不能突破 AutonomyPolicy、Constitution、ResourceBudget。
23. Survival 或 capability need 不能提升 operator authorization ceiling。
24. Secrets 不进入 Runtime snapshot、Network Evidence 或 Skill memory。
25. 外部 Agent/persona 不得覆盖 persistent identity 或 SelfModel identity。

## Self Model

26. CapabilityInventory 表示客观可执行/可访问能力。
27. SelfModel.capabilities 表示主体对自身能力的 belief。
28. LOCAL/OWNED/REMOTE_ACCESS/ACQUIRABLE 必须语义区分。
29. 外部 capability claim 不得直接变成高 proficiency。
30. proficiency/confidence 必须逐渐由真实使用 Evidence 更新。

## Persistence

31. 新增持久字段必须向后兼容 v0.2.1 snapshot。
32. dataclass 新字段必须提供 default/default_factory 或提供 migration。
33. 不移动已有持久 dataclass 的 module path，除非有 migration。

## 实施顺序

34. 先完成 VirtualBrain fa6e3f1 Contract Sync。
35. 冻结 Agent Runtime v0.2.1。
36. 实现 v0.2.2 Capability Foundation。
37. 实现 v0.2.3 Resolver / Reuse Before Create。
38. 实现 v0.2.4 Acquisition / Verification。
39. 实现 v0.2.5 Network / Capability Autonomy。
40. 然后才进入 v0.3 Learning。
41. v0.4 Evolution。
42. v0.5 Reproduction。

禁止为了 Capability 解冻 Agent Network Trust Foundation v0.2。

## v0.2.2 DoD

```
Agent knows:    what capability exists locally
Agent knows:    what capability is missing
Agent can persist: capability inventory
Agent records:  capability gap
Agent cannot:   acquire anything yet
```
