# AegisFlow MVP 验收清单

> 阶段 0 的交付物之一：把"什么算完成"固定下来。后续每次阶段验收都对照本清单。

## A. 三条必须能在本地演示的路径

1. **成功路径：** 创建事件 → 调查 → 证据 → 处置计划 → 审批 → 执行 → 验证 → 报告。
2. **拒绝路径：** 调查完成 → 需要审批 → 审批人拒绝 → 不执行写操作 → 保存拒绝记录。
3. **异常恢复路径：** 工具失败或工作流中断 → 保存状态 → 恢复运行 → 不重复执行已成功的副作用。

附加要求：自动化测试、固定评测样本、运行日志、清晰 README。

## B. 阶段 0（准备阶段）完成清单

- [x] Git 仓库就绪，`.gitignore` 忽略 `.env` / `.venv` / 缓存。
- [x] `pyproject.toml`（Python 3.12，依赖按阶段规划）。
- [x] `.env.example`，真实密钥不入库。
- [x] `docker-compose.yml` 提供本地 MySQL 8。
- [x] 最小可运行骨架：`GET /health` + 冒烟测试。
- [x] 四类模拟事件数据 `data/seed/events.json` 与业务数据 `data/seed/business_data.json`。
- [x] 8 份排障手册 `data/knowledge/*.md`（固定 frontmatter 格式）。
- [x] 事件状态 / 运行状态 / 动作状态 / 风险等级 / 运行模式 / 错误分类定义于 `docs/DOMAIN_DESIGN.md`。
- [x] 本验收清单。

## C. 后续阶段完成度（滚动更新）

### 后端
- [ ] FastAPI 事件 CRUD
- [ ] Pydantic 输入 / 输出校验
- [ ] SQLAlchemy + MySQL 持久化
- [ ] Service / Repository 分层
- [ ] 统一异常、配置与日志
- [ ] pytest API 与数据库测试

### LangGraph
- [ ] State、节点、条件路由
- [ ] 调查循环与停止条件
- [ ] Checkpointer
- [ ] 人工中断与恢复
- [ ] 成功 / 失败 / 拒绝 / 预算耗尽分支测试

### Harness
- [ ] Runtime / Run Manager
- [ ] Tool Registry / Tool Executor
- [ ] Context Manager
- [ ] Policy Engine
- [ ] Budget Manager
- [ ] Error Handler
- [ ] Output / Evidence Validator
- [ ] Audit 与 Trace

### RAG
- [ ] 本地知识文档（已有初稿，待接入加载/切分/检索）
- [ ] 关键词 / BM25 检索
- [ ] 证据来源追踪
- [ ] 固定检索评测集

### 安全与可靠性
- [ ] 工具白名单
- [ ] 参数与资源范围校验
- [ ] 风险分级 + 高风险必须审批
- [ ] 审批绑定动作版本
- [ ] 幂等写操作 + 执行后验证
- [ ] 结果未知时不盲目重试
- [ ] 提示注入与越权测试

### 作品集
- [ ] 本地启动指南（README 已有初稿）
- [ ] 架构图与 LangGraph 状态图
- [ ] 工作台
- [ ] 成功 / 拒绝 / 恢复演示脚本
- [ ] pytest 报告、Agent / RAG 评测结果
- [ ] 已知限制与后续计划
