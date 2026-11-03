# 知忆 Zhiyi AI

面向个人文档与跨会话信息的 RAG、长期记忆和 Agent 问答系统。

知忆将“文档知识”和“用户记忆”分成两条可独立验证的数据链：文档经过父子分块后进入 Elasticsearch，兼顾关键词与语义检索；对话中的事实被抽取为陈述、实体、关系和事件，写入 Neo4j。问答时，大模型通过受控工具调用按需读取两类信息，后端负责鉴权、参数校验、工具执行和结果回灌。

> 项目用于工程学习与作品展示。当前为本地单节点部署方案，评测结果来自小规模自建数据集，不代表生产环境容量或线上准确率。

2026-10-27 更新的记忆治理、证据校验、任务补发与 Agent 执行约束说明见 [实施与面试说明](docs/upgrade-20261025/实施与面试说明.md)。当前默认每用户最多 1,000 条活跃陈述、10,000 个图节点，这两个值可配置，未经生产容量评估。按当前简历逐条核对的项目讲稿、去重例子、评测口径和追问答案见 [2026-11-03 简历项目讲解](docs/interview/20261103_RESUME_PROJECT_WALKTHROUGH.md)；[旧讲解](docs/UPGRADE_INTERVIEW_GUIDE.md)保留历史阶段记录。

涉及资料或个人事实的答案会先缓存候选正文并检查证据，再以 SSE 分段发送；工具状态仍可先发送。因此首字等待会增加，不应将它描述为模型一生成 token 就立即显示。

## 项目亮点

| 模块 | 设计与实现 |
| --- | --- |
| 知识库 RAG | 文档异步解析、父子分块、BM25 + 向量混合检索、父块回取、来源引用 |
| 长期记忆 | Dialogue → Chunk → Statement → Entity / Relation / Event，可追溯原始对话 |
| Agent | Function Calling、ReAct 降级、工具参数校验、用户上下文注入、循环上限与 SSE 事件流 |
| MCP | 可选、默认关闭；按用户加载已授权 Server，支持工具发现、名称隔离与连接超时 |
| 模型管理 | OpenAI 兼容接口，对话、Embedding、Rerank 与联网搜索配置，API Key 加密保存 |
| 工程验证 | 自动化测试、检索/记忆/答案分层评测、Docker Compose 本地运行环境 |

## 系统架构

```text
                                      ┌──────────────────┐
                                      │    PostgreSQL    │
                                      │ 用户/文档/任务/对话 │
                                      └────────▲─────────┘
                                               │
┌──────────────────┐    HTTP / SSE    ┌────────┴─────────┐
│ React + TypeScript├─────────────────►│     FastAPI      │
└──────────────────┘                  └───┬────┬────┬─────┘
                                         │    │    │
                         ┌───────────────┘    │    └──────────────┐
                         ▼                    ▼                   ▼
                 ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
                 │Celery + Redis│    │Elasticsearch │    │    Neo4j     │
                 │ 异步解析/抽取 │    │BM25 + Vector │    │  长期记忆图谱 │
                 └──────────────┘    └──────────────┘    └──────────────┘
```

一次 Agent 问答的主要流程：

```text
用户问题
  └─► 上下文组装
       └─► LLM 判断是否调用工具
            ├─► knowledge_search：检索个人文档
            ├─► memory_search：召回长期记忆
            └─► MCP tool：调用已授权的外部工具
                  └─► 后端校验并执行
                       └─► ToolMessage 回灌模型
                            └─► SSE 返回答案、工具状态与引用
```

## 核心设计

### 1. 父子分块与混合检索

- 子块用于检索，提高局部事实的命中精度。
- 命中后按 `parent_id` 回取父块，为生成模型补充完整上下文。
- BM25 处理专有名词、错误码等关键词查询，向量检索处理同义表达。
- 默认用 RRF 按两路候选名次融合，避免直接混合 BM25 与向量的原始分数；加权融合仍保留作对照。可选 Rerank 只有配置对应模型后才执行。

### 2. 可追溯的长期记忆

- 保留原始对话与分块，不只保存模型抽取出的结论。
- 将原子事实建模为 Statement，再连接 Entity、Relation 和 Event。
- 检索结合向量、全文与图邻居，并按 `user_id` 做数据隔离。
- 召回结果经过数量、长度、相似度和置信度限制，减少无关记忆污染上下文。

### 3. 受控的 Agent 工具循环

- 模型只生成工具名与参数，实际工具由后端查找和执行。
- 用户身份、数据库会话和知识库范围由服务端注入，不能由模型伪造。
- 工具失败会以错误状态回灌模型；单次问答最多五次模型工具轮，工具调用总数另受配置预算限制，防止无限循环。
- 支持知识库工具、记忆工具和按用户配置的 MCP 工具。

### 4. 结构化输出可靠性

长期记忆抽取不仅检查 JSON 能否解析，还检查模型结束原因、空正文、Schema 和最低业务结果。格式错误进入有限重试或失败状态，不将空数组静默标记为成功。针对 DeepSeek 兼容接口的推理输出与正文预算问题，项目包含对应回归测试。

## 技术栈

- 前端：React 18、TypeScript、Vite、Ant Design、AntV X6
- API：Python 3.12、FastAPI、Pydantic、SQLAlchemy、Alembic
- Agent：LangChain、Function Calling、ReAct、MCP Adapter、SSE
- 数据：PostgreSQL 16、Elasticsearch 8、Neo4j 5、Redis 7
- 异步任务：Celery
- 工程化：uv、pytest、Docker Compose

## 离线评测

当前仓库提供固定语料、问题和相关性标注，可复现检索与记忆评测流程。下表是旧版小集，保留作历史基线；当前 PDF 简历使用的是后续 100 文档、记忆问法和端到端回归结果。

| 任务 | 数据规模 | Recall@5 | MRR | nDCG@5 |
| --- | ---: | ---: | ---: | ---: |
| RAG 混合检索 | 10 篇语料 / 22 题 | 1.0000 | 1.0000 | 0.9944 |
| 长期记忆检索 | 19 题 | 0.6053 | 0.7707 | — |

上表是旧版 10 篇/22 题 RAG 与 19 题记忆集的历史结果。2026-11-03 已用真实 Embedding 与 ES 重跑 100 篇文档的 100 道标题明确问题：纯向量 Recall@5/MRR/nDCG@5 为 1.00/1.00/1.00，RRF 为 1.00/0.9650/0.9742；17 段对话的 100 种问法（19 组事实）记忆 Recall@5 为 0.95。另有 20 份合成文档、30 题真实 Agent/Verifier/SSE 回归。样本较容易且多有相关改写，不能推断开放问答准确率；完整边界和失败项见 [最新验收记录](docs/evidence/100-document-evaluation-20261102.md)。

评测方法和运行参数见 [api/eval/README.md](api/eval/README.md)；本轮解析、分块、混合权重与答案证据校验的实测边界见 [RAG 质量与参数实验](docs/RAG质量与参数实验_20261029.md)。

## 快速开始

### 环境要求

- Docker 与 Docker Compose
- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- Node.js 18+

### 1. 启动依赖

```bash
cp .env.example .env
docker compose up -d postgres elasticsearch neo4j redis
```

### 2. 启动 API

```bash
cd api
cp .env.example .env
uv sync --group dev
uv run alembic upgrade head
uv run python run.py
```

API 默认地址为 `http://localhost:8000`，OpenAPI 文档位于 `http://localhost:8000/docs`。

### 3. 启动异步任务

另开一个终端：

```bash
cd api
uv run celery -A app.celery_app.celery_app worker \
  -l info -Q default,parse,memory --pool=threads --concurrency=6
```

### 4. 启动前端

```bash
cd web
npm ci
npm run dev
```

访问 `http://localhost:5173`，注册账号后至少配置一个对话模型和一个 Embedding 模型。

也可以使用 Docker 构建全部服务：

```bash
docker compose up -d --build
```

## 配置说明

根目录 `.env` 供 Docker Compose 使用，`api/.env` 供本地启动的 API 使用。两个文件都由示例文件复制，不应提交真实密钥。

生产或公开部署前至少应修改：

- `JWT_SECRET`：JWT 签名密钥。
- `FERNET_KEY`：模型 API Key 的加密密钥。
- `POSTGRES_PASSWORD`、`NEO4J_PASSWORD`：存储密码。
- `CORS_ORIGINS`：允许访问 API 的前端来源。

## 测试

```bash
cd api
uv run pytest -q

cd ../web
npm run build
```

2026-11-02 验收：后端 `97 passed`，Ruff 检查与前端生产构建通过。100 篇独立文档、100 道检索题和记忆回归结果见 [实测记录](docs/evidence/100-document-evaluation-20261102.md)。这些离线题包含标题线索，指标不能代表开放场景的回答准确率。

本机 Docker 演示可运行 `./scripts/start_local_demo.sh`；它使用独立端口：Web `http://127.0.0.1:5174/test-client`、API `http://127.0.0.1:8008/docs`。脚本会启动本地 Embedding 服务和 Docker 容器，聊天仍需在环境中配置可用模型。若在 WSL 中运行，请先检查 Windows 到 WSL 的端口转发，再从 Windows 浏览器访问。

## 目录结构

```text
api/
├── app/controllers/       API 与 SSE 接口
├── app/services/          业务编排
├── app/core/rag/          解析、分块与混合检索
├── app/core/memory/       抽取、去重、召回与图模型
├── app/core/agent/        工具注册、Agent 循环与追踪
├── app/repositories/      PostgreSQL / Elasticsearch / Neo4j 数据访问
├── eval/                  离线评测脚本、语料与标注
└── tests/                 自动化回归测试
web/                       精简后的 React 前端
docker/es/                 Elasticsearch IK 分词镜像
docker-compose.yml         本地编排
docker-compose.prod.yml    资源受限服务器的覆盖配置
```

## 当前边界

- PostgreSQL、Elasticsearch、Neo4j 和 Redis 默认都是单节点，仅用于学习与功能验证。
- Query 改写、精确 token 预算和持久化 Agent checkpoint 尚未形成完整实现。
- MCP 是扩展能力，主要验证链路仍是知识库与长期记忆工具。
- 当前 100 道检索题和 100 种记忆问法规模仍有限，尚未建立线上反馈集及系统性的答案准确率、拒答率人工评测。
- 前端保留在线上传、RAG 问答、记忆确认和研究成果保存等验证入口。

## AI 协作说明

仓库的整理与改造由作者和 ChatGPT、Codex 协作完成。AI 用于需求拆分、跨文件检索、候选代码、故障分支枚举和测试补充；功能范围、方案取舍、代码审查、测试执行与结果验收由作者负责。所有生成内容都应以实际代码、日志、自动化测试和离线指标为准，不能将 AI 输出直接视为正确实现。

## 来源与发布说明

本项目从上游 Comet 的 RAG、长期记忆与 Agent 代码中整理而来，进行了功能收缩、品牌清理、模型兼容与记忆可靠性修复、测试和离线评测。完整来源、基准提交与个人改动范围见 [UPSTREAM_ATTRIBUTION.md](UPSTREAM_ATTRIBUTION.md)。

这不是从零原创的通用 AI 框架。整理时未在上游仓库发现明确的许可证；本仓库保留来源与改动说明，但来源声明本身不构成重新授权。使用或再次分发相关代码前，请自行核对上游许可状态。
