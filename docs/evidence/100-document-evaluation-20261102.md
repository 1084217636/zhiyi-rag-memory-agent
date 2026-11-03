# 知忆新版功能与评测证据（2026-11-02）

## 语料与方法

`api/eval/fixtures/corpus_100/` 有 100 个不同页面的冻结中文维基百科摘要，逐篇保留来源 URL 和 CC BY-SA 署名信息；`corpus_100_manifest.json` 记录 100 个不同 page ID，`gold/retrieval_100docs.json` 给每篇文档一个标题明确的查询。自动测试校验文件、来源与 gold 一一对应。评测写入隔离用户下的原 10 份加新增 100 份文档，复用应用实际父子分块、bge-m3 1024 维向量和 Elasticsearch 索引。问题包含标题，因此这是一组容易的已知文档检索题，不代表复杂推理或最终答案质量。

## 本地实测

| 任务 | 样本与结果 |
|---|---|
| 100 文档真实检索 | 升级后重新写入 110 篇（原 10 篇加新增 100 篇），同一批 100 题：纯向量 Recall@5 1.00、MRR 1.00、nDCG@5 1.00；BM25 为 1.00/0.8637/0.8984；RRF 为 1.00/0.975/0.9815；加权融合为 1.00/0.99/0.9926。报告 `report-20261102-211641.md`。本题集纯向量更好，不能宣称 RRF 在所有任务中获胜。 |
| 记忆召回 | 升级后重新萃取 17 段真实对话到隔离 Neo4j 用户，100 条问法对应 19 个事实组；按活跃 Statement 事实锚点评分，Recall@5 0.95、Precision@5 0.19、MRR 0.8133、nDCG@5 0.8483。报告 `report-20261102-211553.md`；5 条未命中。题目有改写重复，不能当 100 个独立事实。 |
| 抽取质量 | 10 段人工 gold：实体 F1 0.8169，三元组 F1 0.6167。 |
| 批内实体去重 | 3 组人工 gold：Pairwise Precision 1.00、Recall 0.5556、F1 0.6508；同义别名漏合并明显。 |
| 在线功能 | 新用户经 HTTP 上传一份 `.md`，Celery 解析状态到 `done`，ES 搜索返回 3 个片段；真实 Agent SSE 两次工具调用后返回 token、引用和 `done`；将该回答及引用保存为研究成果、版本 1→2 更新后列表可见。 |
| React 页面 | Playwright 1.56.1 在真实浏览器里完成注册/登录、文件上传等待 Celery、创建会话、SSE 问答与引用展示、保存研究成果，以及冲突记忆确认与来源删除；无页面异常。 |
| 跨会话记忆 | 另一新用户在第一会话说正在研究 WiFi CSI；回答成功后 Celery 抽取，Neo4j 搜索返回 2 条相关记忆；第二会话提问时真实 Agent SSE 召回并在答案中提到 CSI。 |
| 自动化 | `pytest -q` 97 个测试、Ruff、前端 `npm run build` 与 API/Web Docker 镜像构建通过。Web 构建在本地使用可配置的镜像代理；默认 Docker Hub 元数据请求曾超时。 |
| 指标接口 | 运行中的 `/api/metrics` 返回真实 HTTP 请求计数与耗时分桶，包含上传、聊天 SSE 等已执行路由；Prometheus/Grafana 配置已提供，本轮未在和 LinkGo 端口冲突的条件下再次启动知忆的面板。 |

检索、记忆、抽取、去重的逐题 JSON 与时间戳报告保存在被 Git 忽略的 `api/eval/results/`。Rerank 为可选配置，本轮没有可用精排模型，未生成真实 Rerank 指标。最终答案的准确率、无证据拒答率与引用事实支持率需要按冻结问题和人工标注另测；一次在线问答通过不能冒充系统性答案评测。

## 记忆治理升级与实测

同一用户的当前工作城市、居住城市使用保守的属性槽位召回旧事实，槽位相同的候选即使向量相似度低也进入比较；未知或可并存事实仍走语义候选。模型提出新增、重复、更新、冲突或跳过，服务端限制目标 ID 和更新条件。没有明确更正时，同一当前城市的第二个取值不能直接成为另一条活跃事实。计划里的明确日期（含“六个月后”）优先于默认 30 天期限。

`api/eval/verify_memory_governance.py` 在 Docker API、Celery、Neo4j、PostgreSQL、Redis 和真实对话/Embedding 模型下创建隔离用户，实测：杭州旧事实 `ADD`；深圳新事实 `CONFLICT` 并进入待确认；用户确认替换后旧版 `superseded`；删除新来源后旧版恢复为活跃；再次投递已删除来源的 Celery 任务，PostgreSQL 仍为 `deleted` 且来源文本为空。操作记录可由 `/api/memories/policy/operations` 查询；浏览器通过三个确认按钮和来源删除按钮完成相同交互。Neo4j 来源专属陈述、事件、关系与来源节点在事务中清理，并留下墓碑阻止迟到任务复活。

删除接口的范围是**该条来源记忆和其可定位的派生图数据**。原聊天消息仍按聊天记录保留；与其他来源合并的共享实体描述、历史备份和外部模型提供方的数据不能由这个接口保证彻底清除。不能把它介绍成跨所有存储的完全物理删除。

## 复现入口

```bash
cd api
.venv/bin/pytest -q
.venv/bin/python -m eval.run_eval --only retrieval
.venv/bin/python -m eval.run_eval --only memory
.venv/bin/python -m eval.run_eval --only extraction --skip-setup
.venv/bin/python -m eval.run_eval --only dedup --skip-setup
.venv/bin/python -m eval.verify_online_upload --chat
.venv/bin/python -m eval.verify_online_memory
CELERY_BROKER_URL=redis://127.0.0.1:6381/1 CELERY_RESULT_BACKEND=redis://127.0.0.1:6381/2 .venv/bin/python -m eval.verify_memory_governance
```

页面验收脚本是 `api/eval/verify_web_ui.cjs`，在本机用 Vite 代理到 API 后运行；需要 Playwright Chromium。它在页面内操作，不通过直接 HTTP 调用冒充前端交互。本机借用 LinkGo 已安装的 Playwright 包并在官方容器中执行：

```bash
cd /home/xiaobin/myproject/zhiyi-research-next
docker run --rm --network host --user "$(id -u):$(id -g)" \
  -v "$PWD:/work" \
  -v /home/xiaobin/myproject/linkgo-collab-next/scripts/browser/node_modules:/tmp/browser/node_modules:ro \
  -w /work -e PLAYWRIGHT_MODULE=/tmp/browser/node_modules/playwright \
  mcr.microsoft.com/playwright:v1.56.1-noble node api/eval/verify_web_ui.cjs
```

需要 Elasticsearch、Neo4j、PostgreSQL、Redis、Celery worker、bge-m3 embedding 和有效对话模型；`eval/.env.eval` 为本地忽略文件。在线验收在 `http://127.0.0.1:8008` 的开发服务进行，脚本每次注册新用户，不改真实用户数据。实际在 LinkGo Redis 占用 6379 时，将知忆测试 Redis 独立放在 6380，API 与 worker 使用相同的 broker 地址。

目前可用 `./scripts/start_local_demo.sh` 启动知忆 Docker 演示，Web 映射到 5174、API 到 8008、Redis 到 6381，Ollama Embedding 由用户级 systemd 服务运行在 11435。上述 6380 是上一轮独立主机进程测试时的端口；Docker 演示使用 6381。页面与 API 在 WSL 内已验证返回 200。Windows 侧到 WSL 端口的连接在本机超时，属于宿主网络转发/防火墙限制，不能因为 WSL 内 200 就说 Windows 浏览器已可访问。

## 2026-11-03 最后一轮后端验收

- 真实检索链路增加双路容错：向量故障用 BM25，BM25 故障且向量有命中时保留向量结果；双路都故障时抛出错误，由 Agent 报告工具失败，不能伪装成“没有资料”。三个故障分支由 `test_rag_branch_failover.py` 验证。
- 聊天接口在返回 SSE 响应头之前核对 `conversation_id` 归属；陌生人的会话 ID 返回 HTTP 404，不再在流开始后静默创建新会话。单元测试和真实双账号请求均覆盖。
- `eval.verify_agent_boundaries` 使用两个新建隔离账号，经 API、Celery、Elasticsearch 和真实对话模型完成 5/5 项验收：有据答案含正确引用、资料未记载的数值不被编造、跨用户文档与会话不可访问、恶意文档指令未被执行。报告在被 Git 忽略的 `api/eval/results/agent-boundaries.json`。这 5 项是边界冒烟测试，不能推断总体幻觉率或安全攻击成功率。
- `eval.verify_online_upload --chat` 再次完成上传→解析→工具检索→Verifier→SSE 引用→研究成果保存与版本更新。`eval.verify_online_memory` 再次完成首轮对话→Celery 抽取→Neo4j 跨会话召回→新会话回答。
- Ruff 全量通过；`pytest -q api/tests` 为 **101 passed**。本轮 100 文档检索复跑：纯向量 Recall@5/MRR/nDCG@5 为 1.00/1.00/1.00，BM25 为 1.00/0.8537/0.8910，RRF 为 1.00/0.9650/0.9742，加权融合为 1.00/0.9900/0.9926；报告 `report-20261103-102537.md`。这批问题仍是标题明确的容易题，不能声称 RRF 优于纯向量或加权融合。
- 评测进程第一次连接旧端口 11434 超时；覆盖 `EVAL_EMBED_BASE_URL=http://127.0.0.1:11435/v1` 后复跑通过。本地忽略的 `eval/.env.eval` 应按当前 Ollama 端口更新；在线 Docker API 已使用正确端口。
- `eval.verify_grounded_answer_set` 在新建隔离用户下上传 20 份合成 Markdown，经实际 Celery/ES/Agent/Verifier/SSE 运行 30 题：20 道明确数值题全部返回正确数值并引用所属文档，10 道明确未记录备用数值题全部说明无法给出。20/20 引用指向预期文档；端到端耗时 P50 6.78 秒、P95 8.07 秒，整组墙钟时间 287.59 秒。逐题原文与判定在被 Git 忽略的 `api/eval/results/grounded-answers-30.json`。题目带文档名、内容为固定模板，判分采用确定性字符串/来源 ID 规则；这些数字只证明该合成回归集通过，不是开放问题准确率、人工事实支持率或线上延迟 SLO。
