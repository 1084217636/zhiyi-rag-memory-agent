# Zhiyi AI 离线评测（eval）

用业界标准指标，**自包含、可复现**地评测 RAG 检索与记忆系统（抽取 / 去重 / 检索）。

> 自带语料和标注、自己写入再评测，**不依赖现有用户数据、不读 app 的模型配置表**——直接填模型 key 就能跑。
> 不进生产镜像（`api/.dockerignore` 已排除 `eval/`），但随仓库保留，便于复现。

---

## 设计要点

- **独立配置**：`eval/.env.eval` 直接填 embedding/chat/rerank 的 base_url+model+key，用 `LLMClient` 直接建客户端，不依赖系统里建用户/配模型。
- **固定命名空间**：所有评测数据写在 `EVAL_USER_ID`（固定 UUID）名下，与真实用户隔离，可一键清理。
- **写入到评测全闭环**：`setup` 复用 app 真实的分块/向量化/萃取链路把 fixtures 写进 ES/Neo4j（顺带也验证了写入链路），再评测。
- **双输出**：① 数值报告（Markdown 指标表）② 明细（JSON：每题召回了啥/命中没、每段抽了啥 vs gold），看明细可定位问题、调系统策略。

---

## 目录

```
eval/
├── .env.eval.example     模型配置模板（复制为 .env.eval 填 key）
├── eval_config.py        读 .env.eval 建 client + EVAL_USER_ID
├── metrics.py            标准指标：Recall@k/Prec@k/MRR/nDCG、集合P/R/F1、Pairwise F1
├── clients.py            ES 检索变体（纯向量/BM25/混合/+rerank）+ 客户端清理
├── fixtures/             自带测试数据
│   ├── corpus/*.md       原有 10 份知识库语料
│   ├── corpus_100/*.md   新增 100 份独立来源文档（维基百科摘要，含来源）
│   ├── dialogues.json    对话（萃取进 Neo4j）
│   └── gold/             标注集 retrieval / memory_retrieval / extraction / dedup
├── pipeline/
│   ├── setup.py          写入：语料→ES、对话→Neo4j（EVAL_USER_ID 名下）
│   └── teardown.py       清理：删 EVAL_USER_ID 的 ES + Neo4j 数据
├── tasks/
│   ├── retrieval.py      RAG 四配置 + 记忆检索
│   ├── extraction.py     抽取 实体级/三元组级 P/R/F1
│   └── dedup.py          去重 Pairwise P/R/F1
├── reporters.py          输出报告 + 明细
├── run_eval.py           ⭐ 总入口
└── results/              报告与明细（git 忽略）
```

## 自带数据集场景

对齐业界标准评测的题型设计，开箱即可跑：

- **RAG 检索**：保留原 10 篇文档/22 道审核题与 `gold/retrieval_100.json` 表述变体回归集；新增 `corpus_100/` 中 100 篇不同页面的冻结摘要和 `gold/retrieval_100docs.json` 一文一题。实际检索评测会把 110 篇都写入隔离 ES 命名空间，再用新增 100 题比较纯向量、BM25、RRF 与加权融合。新题在问题中直说标题，难度较低；其 100 篇独立来源并不意味着 100 道高难推理题。原 100 条改写题仍不是独立知识点。
- **长期记忆召回**：初始 `gold/memory_retrieval.json` 为 19 个事实问题；`gold/memory_retrieval_100.json` 是 100 条表述变体，覆盖 19 个事实组。新治理路径直接返回活跃 Statement，评测读取其中的 `statements[].statement`，用 `gold/memory_retrieval_100facts.json` 的人工事实锚点检查是否召回支撑陈述，不再按统一展示名“用户记忆”评分。锚点是便于回归的词组规则，不能替代人工审阅最终回答。
- **记忆系统**(中文长对话个人陈述):`fixtures/dialogues.json` 是同一人设跨多段的个人陈述(上海产品经理、老家成都、复旦新闻系、养布偶猫团子、爱爬山摄影、学日语、喝拿铁、用 iPhone+索尼相机、妹妹林晓在成都);gold 的抽取三元组**严格使用受控词表谓词**(属于类型 / 位于 / 拥有 / 偏好 / 了解 / 使用 / 关联于…)。**项目记忆萃取流水线为中文优先**,prompts 与受控词表全中文,英文场景不在评测覆盖内(原计划接入的 LongMemEval-S 已下架,避免翻译噪声与实体名失真)。

> 想换成贴合自己数据的场景，直接替换 `fixtures/` 下对应文件即可（语料文件名即 `relevant_doc_ids`）。

## 准备

1. 起存储：`docker compose up -d postgres elasticsearch neo4j redis`。
2. 复制 `eval/.env.eval.example` → `eval/.env.eval`，填 embedding（必需）、chat（仅生成/抽取评测需要）、rerank（可选）的 key。本地 `zhiyi-ollama.service` 使用 11435 端口；在主机上直接跑评测时，Embedding 地址应指向 `http://127.0.0.1:11435/v1`。
3. （可选）把 `fixtures/` 的语料/对话/gold 换成你自己的，更贴合真实数据。

## 运行（在 api/ 目录）

```bash
uv run python -m eval.run_eval                  # 全流程：模型自检 + 写入 + 全部评测（保留数据）
uv run python -m eval.run_eval --reset          # 重跑：先清空旧数据再写入（推荐，记忆写入非幂等）
uv run python -m eval.run_eval --skip-setup     # 数据已写过，直接评测
uv run python -m eval.run_eval --skip-check     # 跳过模型可用性自检
uv run python -m eval.run_eval --only retrieval # 只跑 RAG 检索（retrieval/memory/extraction/dedup）
uv run python -m eval.run_eval --teardown       # 跑完清理评测数据
uv run python -m eval.chunk_ablation            # 比较 128/256/384 子块，最后恢复默认评测索引
uv run python -m eval.tune_retrieval --collect   # 冻结候选并比较权重/RRF，不改线上配置
uv run python -m eval.grounded_metrics results/人工标注答案.jsonl  # 有答案标注后才计算生成指标
uv run python -m eval.verify_agent_boundaries       # Docker API：隔离用户的答案/越权/恶意文档验收
```

> 正式跑前会先做**模型可用性自检**：分别调用 embedding / chat / rerank 确认连得通（embedding 还会校验维度是否与 ES 索引一致），不通的必需模型直接中止、rerank 不通则自动跳过其对比列，避免灌了一半数据才发现 key/url 写错。
> 全程带**进度日志**（写入第几篇语料 / 第几段对话萃取、评测第几题），方便看卡在哪一步。

结果在 `eval/results/`：`report-时间.md`（指标表）+ `details-时间.json`（逐条明细）。

## 指标

| 任务 | 指标 |
|------|------|
| RAG 检索（四配置）| Recall@k、Precision@k、MRR、nDCG@k |
| 活跃陈述召回 | 事实锚点命中的 Recall@k、Precision@k、MRR、nDCG@k |
| 三元组抽取 | 实体级 / 三元组级 Precision、Recall、F1 |
| 实体去重 | Pairwise Precision、Recall、F1 |

## 诚实声明

均为**小规模自建 gold 集**的离线自测，非大规模公开 benchmark。简历/汇报请如实标注样本规模与评测方式。

## Agent 最终回答与故障评测（新增）

`grounded_metrics.py` 读取**冻结的真实运行输出 + 人工标注** JSONL；不会自己调用模型，也不会用另一个 LLM 的一句“正确”当标准答案。每行至少有 `case_id`、`case_type`、`answerable`、`refusal`。可补 `gold_evidence_ids`、`cited_evidence_ids`、逐条 `claim_support`、`answer_correct`、`expected_tools`/`called_tools`、`task_complete`、`tool_failed`/`tool_failure_disclosed`、`unsafe_action_executed`、`tenant_data_leaked`、`latency_ms`、`total_tokens`、`cost_usd`。标注缺失的指标输出 `null` 并报告覆盖量，不把缺失当 0。

先冻结同一批问题与来源版本，在隔离评测用户运行两版系统，保存模型/Prompt/索引/工具版本、原始输出、Trace 和耗时。人工逐题核对答案事实、引用是否真的支撑断言、工具是否选对、无答案是否拒答、工具失败是否如实说明。对难判的样例保留争议并复核，不能只看总分。题型至少分为普通有据、无证据、证据冲突、工具超时、跨用户资料、恶意文档指令、长对话、记忆冲突；每类分别报告样本量与结果。

`build_agent_casebook.py` 从已有 22 道 RAG 和 19 道记忆检索题生成带稳定 ID 的问题册，另加 8 道故障/边界题，共 49 道。它只生成**待运行问题**，不生成模型答案；其中无证据、冲突、超时、跨用户和长上下文等题需要按题意先布置环境。`gold_document_ids` 与 `gold_entity_names` 是检索标注，不能直接冒充最终回答的引用 ID。

```bash
cd api
uv run python -m eval.build_agent_casebook --output eval/results/agent-casebook.jsonl
```

`capture_agent_runs.py` 通过真实 `/api/conversations` 和 `/api/chat/stream` 请求，把 SSE 中的回答、工具事件、引用、Trace ID、错误和耗时写成原始 JSONL。先给**专用评测账户**上传相同语料并配置模型；不要使用真实用户账户。令牌只从环境变量读取，脚本不打印它；输出可能包含文档内容，留在被 Git 忽略的 `eval/results/`。默认只跑 22 道 RAG 题，关闭自动记忆写入；记忆题需要新鲜隔离账户或逐题重置，因为当前问答会把输入提交给记忆抽取。8 道故障题需要各自注入条件，脚本不会假装它们已执行。

```bash
export ZHIYI_EVAL_TOKEN='评测专用账户的访问令牌'
uv run python -m eval.capture_agent_runs \
  --base-url http://localhost:8000 \
  --casebook eval/results/agent-casebook.jsonl \
  --output eval/results/agent-raw-版本A.jsonl --limit 2
```

采集后人工把每条原始输出和对应问题、证据对照，填写 `answerable/refusal/answer_correct/claim_support` 等评分字段，再用下面的评分器计算。采集脚本不自动判断回答是否正确，也不会用关键词猜“拒答”。本地已用 HTTP 替身验证事件解析与关闭记忆开关；**没有用真实账号跑完整 49 题**。

```bash
cd api
uv run python -m eval.grounded_metrics eval/results/当前版本人工标注.jsonl \
  --baseline eval/results/旧版本同题人工标注.jsonl \
  --min-cases 30 --output eval/results/agent-compare.json
```

`--baseline` 强制两版 `case_id` 集合相同，输出整体、按题型分组和差值。`--min-cases` 防止拿几个案例冒充稳定指标；它不是统计显著性证明。安全和租户泄露事件即使整体平均不错，也需要逐例查原因。若新旧题集或知识库版本不同，不做直接差值比较。

`eval/fixtures/agent_eval_demo.jsonl` 的 6 条是**人工编写的评分器演示数据**。CI 运行它只是检查字段、公式和命令能工作，文件中所有 1.0 等分数**绝不是知忆模型的效果**。目前仍缺一批真实 Agent 运行输出的系统化人工标注，因此不能声称已经测出最终回答准确率、幻觉率或运维故障定位率。

## CI 与交付边界

仓库的 `.github/workflows/ci.yml` 在 push/PR 上跑锁定依赖安装、Ruff、pytest、评分器演示、前端构建、Docker 构建和 Compose 配置检查；在 `main` 手动触发时，检查通过后才尝试把 commit SHA 镜像发布到 GHCR。本地于 2026-11-02 验证了 Ruff、97 个测试、Compose 配置、API 和 Web 镜像构建。Web 镜像构建使用了可配置的镜像代理，以避开当时 Docker Hub 元数据请求超时。**没有 GitHub runner 的成功记录、没有生产环境自动部署或回滚记录**。独立运行的真实 ES/Neo4j/Celery 评测仍不在轻量 CI 中，需要单独的集成环境、评测模型和隔离用户。
