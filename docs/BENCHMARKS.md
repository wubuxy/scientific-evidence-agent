# 复现实验

工具链比较同模型、同资料和可记录预算下的检索与证据处理方法。实验覆盖给定论文集合的短答案和字段生成，复用科研后端的 Store、ToolBus、检索、阅读与上下文组件。

## 冻结数据

| 数据 | 准备方式 | 用途 |
|---|---|---|
| [QASPER v0.3](https://huggingface.co/datasets/allenai/qasper) | 官方 dev 选 50 题、test 选 200 题，seed=20261104；保留文本可定位任务和不可回答题 | 论文问答与证据定位 |
| [ArxivDIGESTables](https://huggingface.co/datasets/blnewman/arxivDIGESTables) | 固定 revision `3f8eb69470189d79fb9dba7dd2764a2328b7b0c1`；2–5 篇论文、2–4 列的字段任务，隐藏答案表与题注 | 跨论文比较的数据适配与标注 |

QASPER 的开发 / 测试集分别来自 44 / 162 篇论文，论文互不重叠；这是过滤后的文本子集。ArxivDIGESTables 提供开发 10 任务与测试 40 任务，划分间隔离论文和来源文章；使用 `review` 导出任务范围、参考字段与证据的标注队列。改编依据见 [原论文 §5.2](https://arxiv.org/html/2410.22360v1)。

每个划分包含 `inputs.json`（正文与问题）、`gold.json`（答案与证据标注）、`manifest.json`（版本、规则、ID、SHA256）。正文统一换行为可定位的文本，所有检索方法共享。生成只读取 inputs，gold 由评分程序单独使用。

## 准备与运行

在 `backend` 目录执行，先按 README 配置 `.env.research`。PowerShell 设置 `$env:PYTHONPATH='app'`，Linux/macOS 对应 `export PYTHONPATH=app`。数据与原始运行目录默认由 Git 忽略。

```powershell
python -m research_backend.benchmarks.download --out ../benchmark-data/raw
python -m research_backend.benchmarks.prepare --raw ../benchmark-data/raw --out ../benchmark-data/qasper-v1
python -m research_backend.benchmarks.run --inputs ../benchmark-data/qasper-v1/dev/inputs.json --out ../benchmark-runs/qasper-dev --limit 20 --methods grep grep-ledger rag rag-once --max-tokens 20000 --max-steps 10 --concurrency 2
python -m research_backend.benchmarks.score --inputs ../benchmark-data/qasper-v1/dev/inputs.json --gold ../benchmark-data/qasper-v1/dev/gold.json --run ../benchmark-runs/qasper-dev
```

下载器流式处理原始数据，首次下载包含约 1.12 GB 的全文压缩包。冻结目录与运行目录禁止覆盖。可用 `--env-file path/to/.env.research` 指定配置，用 `--task-ids-file ids.json` 指定预先固定的唯一任务 ID 数组；任务数组与 `--limit / --offset` 二选一。留出测试在方案冻结后通过 `--allow-test` 显式开启。

跨论文数据准备与标注导出：

```powershell
python -m research_backend.benchmarks.prepare_digestables --raw ../benchmark-data/raw --out ../benchmark-data/digestables-v1
python -m research_backend.benchmarks.review --inputs ../benchmark-data/digestables-v1/dev/inputs.json --gold ../benchmark-data/digestables-v1/dev/gold.json --out ../benchmark-data/digestables-review.jsonl
```

## 方法与预算

| 方法 | 设置 |
|---|---|
| `grep` | 模型自主正则搜索与按行回读 |
| `grep-ledger` | 新回读后按字段记录已读原文，选择费用计入统一预算 |
| `rag` | 模型自主查询，BM25＋embedding cosine 经 RRF 融合 |
| `rag-once` | 每字段一次 Top-5 混合检索，回读后直接作答 |

默认 `qwen3.5-27b`、temperature=0.1、`enable_thinking=false`；累计 token 预算包括输入与输出，embedding 用量另计并支持缓存。协议保存代码 / 数据哈希、模型、参数与日期。每轮保留所有尝试，评分包含失败与缺失任务；不从重复运行中挑选最好结果。

## 完整轨迹重复与可靠性分析

`benchmarks.trials` 在每个试次重新运行所选 Agent，固定任务、代码与预算，独立读取 gold 评分。短答案报告词面通过的 pass-at-k / pass-all-k、先按任务汇总重复、再按共享论文连接组进行配对 bootstrap；完整报告另报契约、字段和覆盖指标。失败 / 缺失计入分母，不完整成本明确标记，不把词面正确或契约通过解释成语义正确。

计划、运行、评分命令和训练路线见 [Agent 自动评测与训练](AGENT_EVAL_AND_TRAINING.md)。`plan` 和 `score` 不调用模型，`run` 使用已配置的模型服务；新模块没有新增定时收费任务。

## 固定回读对照

从已有 Grep 运行加载原文窗口，检查其与冻结正文一致，再独立比较上下文策略：

```powershell
python -m research_backend.benchmarks.answer_replay --inputs ../benchmark-data/qasper-v1/dev/inputs.json --prior ../benchmark-runs/qasper-dev --policies prefix lexical full --out ../benchmark-runs/context-replay --repeats 2
python -m research_backend.benchmarks.score_replay --inputs ../benchmark-data/qasper-v1/dev/inputs.json --gold ../benchmark-data/qasper-v1/dev/gold.json --run ../benchmark-runs/context-replay
```

`prefix` 保留各已读窗口头部；`lexical` 在已读内容内部使用 4 行窗口、2 行步长、BM25 排序及字段轮流分配；两者均比较 3,500 / 9,000 字符预算。`full` 为扩大预算的完整回读诊断。

按原文行号进行模型选择：

```powershell
python -m research_backend.benchmarks.evidence_replay select --inputs ../benchmark-data/qasper-v1/dev/inputs.json --prior ../benchmark-runs/qasper-dev --source-method grep --methods lines --limit 20 --out ../benchmark-runs/line-selections
python -m research_backend.benchmarks.answer_replay --inputs ../benchmark-data/qasper-v1/dev/inputs.json --prior ../benchmark-runs/qasper-dev --selections ../benchmark-runs/line-selections --policies prefix selected full --out ../benchmark-runs/selected-replay --repeats 2
```

程序按地址复制原文，校验已读范围与资料归属。选择调用成本计入 `selected` 方案，配对分析按任务汇总重复结果。字符保留率度量上下文内容保留，语义支持由单独步骤检查。

## 完整报告与五组重复对照

统一入口 `benchmarks.trials` 同时支持短答案方法、`report-grep` / `report-rag` 和五组 `repair-*` 方法。完整报告调用工作台实际 Runtime；报告与短答案分开制定计划。报告可导出声明和表格事实单元，使用联合原文判定，再通过双人标注和分歧仲裁校准。固定协议、独立评分、成本口径、完整命令与无云调用验收脚本见 [Agent 自动评测与训练路线](AGENT_EVAL_AND_TRAINING.md)。

## 证据支持复核

输入 `cases` 数组，每项包含 `id / question / claim / source_id / evidence`；每条 evidence 提供 `id / source_id / start_line / end_line / quote`。复核只看指定声明与原文，不接触参考标签。仓库提供 16 条合成对照样例，标签独立存放：

```powershell
python -m research_backend.benchmarks.support_review --inputs tests/fixtures/support_review_inputs.json --out ../benchmark-runs/support-smoke
```

输出保存为 `reviews.jsonl`，包括候选判定、理由、程序解析的原文行范围、token 和运行状态。`needs_human_review=true` 标识人工核验步骤；该模块供离线研究使用，不改变默认报告流程。

指标定义与已执行规模见 [验证成果](EVALUATION.md)。

## 核验驱动的一次回读

复用一组已经完成的单论文 Grep 轨迹，分别保存原始答案、仅复核重答、复核后补读重答三组结果。核验区分支持、充分性、方法归属与实验阶段；每个分支的 token 预算包含原始任务和复核调用。

```powershell
python -m research_backend.benchmarks.repair_replay --inputs ../benchmark-data/qasper-v1/dev/inputs.json --prior ../benchmark-runs/qasper-dev --out ../benchmark-runs/review-reread --max-tokens 20000
python -m research_backend.benchmarks.score --inputs ../benchmark-data/qasper-v1/dev/inputs.json --gold ../benchmark-data/qasper-v1/dev/gold.json --run ../benchmark-runs/review-reread
```

`--prior` 必须来自同模型、同 token 上限的 Grep 运行；试验限定单论文、单目标。一次定位最多取 50 个匹配，优先未读位置，再按距离已有锚点排序；最多补读 40 行。输出记录触发、回退、用量与各组实际可见证据，评分保留全部任务。

增加 `--control-only` 并使用新的输出目录，可运行不调用核验模型的规则对照：仅原始回读为空时，由问题生成关键词查询并补读一次。`usage.json` 记录物理上实际新增的调用，分支预测中的成本则包含共享上游费用；两者分别计量。

## 本地核验器标签干预的离线复算

在仓库根目录运行，无需 GPU、模型权重或网络：

```powershell
python scripts/audit_verifier_labels.py --bootstrap
```

复核范围包括六次新训练的 1,446 条预测、两组归档参考、三种标签口径、校准阈值与主要配对区间。完整 GPU 复现需要冻结代码与 v2 原始数据，具体命令、数据位置及未观察到稳定收益的结论见[第三轮结果](results/SCIFACT_LABEL_ABLATION_V1.md)。
