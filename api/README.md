# 知忆 AI 后端

FastAPI 后端按 `controller → service → repository → model/db` 分层，核心代码位于：

- `app/core/rag`：文档解析、父子分块与混合检索。
- `app/core/memory`：结构化记忆抽取、实体融合和召回。
- `app/core/agent`：知识库、记忆、MCP 工具与最多五轮的执行循环。
- `app/tasks/parse.py`：文档解析 Celery 任务。
- `app/tasks/memory.py`：记忆抽取 Celery 任务。
- `eval`：离线指标和回归数据集。

环境变量模板见 `.env.example`。所有模型 API Key 通过登录后的模型配置接口保存，不要写进仓库。
