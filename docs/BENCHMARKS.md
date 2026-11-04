# 证据检索与跨论文比较评测

## 范围

比较相同模型、资料和预算下的多轮 Grep、BM25＋向量 RAG 和字段证据台账。评测短答案与固定字段值生成，复用科研后端的 Store、ToolBus、检索、阅读和上下文投影；未评测网络发现、视觉理解或完整长报告质量。

## 冻结数据

**QASPER v0.3**：官方 dev 抽 50 题、test 抽 200 题，seed=20261104。若任一参考答案依赖图表、证据无法映射，或可回答但没有证据，则排除整题；不可回答题保留。dev 三类排除计数为 162/42/37，test 为 244/82/52。最终 dev 44 篇论文、test 162 篇，无重叠。这是过滤的文本子集，不是完整官方排行榜。

来源：[数据卡](https://huggingface.co/datasets/allenai/qasper)、[官方基线](https://github.com/allenai/qasper-led-baseline)。

**ArxivDIGESTables**：固定 revision `3f8eb69470189d79fb9dba7dd2764a2328b7b0c1`。选高质量表格中 2–5 篇论文、2–4 列、每格一个非空短值的任务，排除 self 行和缺少全文的任务。按原论文 column-names-only value-generation 思路改编，提供论文和字段，隐藏答案表、caption、in-text references。dev 10 任务/31 篇，test 40 任务/110 篇；两个划分的论文和来源文章隔离。划分内任务可能共享论文，单元格不是独立统计样本。

这些划分是待审核候选任务。参考表不是已验证的答案/证据真值。实际开发检查发现：表 `df560e34-f675-447c-b112-dbc47859244d` 的 Upsampling 参考值包含 `JPU (ours)`，可能描述来源文章的改造；表 `6b7fbba6-eced-41ab-ab6c-e467247e1277` 的任务/语言字段可能针对论文中的某个子任务。只给列名时范围不足，不能把不匹配直接归为模型错误。`evidence_status=pending_independent_annotation`，证据指标为空；正式研究前需核对任务范围、参考值和原文依据。当前 ArxivDIGESTables 结果只用于流程诊断。

来源：[论文 §5.2](https://arxiv.org/html/2410.22360v1)、[官方数据](https://huggingface.co/datasets/blnewman/arxivDIGESTables)。

每份冻结数据包含：

- `inputs.json`：文本、段落/行号映射、问题和目标字段，生成进程只读取它。
- `gold.json`：评分进程读取的参考答案及证据。
- `manifest.json`：版本、SHA256、过滤计数、任务 ID 和论文 ID。

正文做空白规范化和 120 字符换行；Grep/RAG 共享文本。本任务给定目标论文集合，不评测全库论文发现。

## 命令

在 `backend` 下执行。PowerShell 先 `$env:PYTHONPATH='app'`；Linux/macOS 使用 `export PYTHONPATH=app`。建议把数据放到仓库外。

```powershell
python -m research_backend.benchmarks.download --out ../benchmark-data/raw
python -m research_backend.benchmarks.prepare --raw ../benchmark-data/raw --out ../benchmark-data/qasper-v1
python -m research_backend.benchmarks.prepare_digestables --raw ../benchmark-data/raw --out ../benchmark-data/digestables-v1
python -m research_backend.benchmarks.review --inputs ../benchmark-data/digestables-v1/dev/inputs.json --gold ../benchmark-data/digestables-v1/dev/gold.json --out ../benchmark-data/digestables-review.jsonl
python -m research_backend.benchmarks.run --inputs ../benchmark-data/qasper-v1/dev/inputs.json --out ../benchmark-runs/qasper-dev --limit 20 --methods grep grep-ledger rag rag-once --max-tokens 20000 --concurrency 2
python -m research_backend.benchmarks.score --inputs ../benchmark-data/qasper-v1/dev/inputs.json --gold ../benchmark-data/qasper-v1/dev/gold.json --run ../benchmark-runs/qasper-dev
```

全文压缩包约 1.12 GB，首次流式解析后缓存派生文本。下载器不执行远程脚本，冻结目录禁止覆盖；更改规则应建新版本。

跨论文实验使用 `digestables-v1/dev/inputs.json`，先 `--limit 2 --max-tokens 40000 --max-steps 14` 验证。方法和提示冻结后，留出测试需显式 `--allow-test`，不要依据测试成绩调参。

## 方法和协议

| 方法 | 设置 |
|---|---|
| grep | 模型自主正则搜索和按行阅读 |
| grep-ledger | 新回读后安排一次 pin_evidence 步骤，按字段保留已读连续原文子串；费用计入同一预算 |
| rag | 模型自主查询，BM25＋embedding cosine 经 RRF 融合 |
| rag-once | 每字段以字段问题做一次 Top-5 混合检索，回读后直接作答 |

台账检查字段、资料归属、已读 evidence ID 和连续原文子串，不判断语义支持，也不提供 gold 信息。无关片段可以提交空 entries；预算不足时跳过记录并保存 ledger_skipped_budget。旧开发版本仅提供工具时出现模型完全不调用它的情况，不能把旧组间差异归因于台账。

主模型 `qwen3.5-27b`，temperature=0.1，`enable_thinking=false`；累计预算包括输入和输出。协议保存代码/数据哈希、模型、参数和日期；trace 保存模型可见消息、公开工具调用和格式解码记录，不保存隐藏推理。embedding 调用与 token 另计，可复用缓存。

每轮实验新建目录；失败、超时、超预算保留，不挑选最好运行。若 provider 将 schema 声明的数组/对象再次编码成字符串，只解码有效 JSON 容器；对象数组误发为一个对象时保留为单元素列表，再严格检查全部目标和引用。原始参数与转换路径保留。不修补无效 JSON、不推测答案。不可回答项允许保留解释文本，评分只依据其显式不可回答标记。

## 指标

- answer_token_f1：参考答案间取最大英文归一化 token F1，先任务内平均，再任务间平均。失败/缺失为 0。不是语义正确率。
- read_paragraph_recall：读取的行与 gold 段落重叠的召回。
- cited_paragraph_overlap_f1：引用覆盖段落与 gold 段落的集合 F1，部分段落命中不代表完整支持结论。
- citation_ids_valid：原文窗口有效性，不是引用支持率。
- citation_semantic_support：默认 null，待独立评判和人工复核。
- 完成率、实际 token、耗时分别报告，缺失用量不当成零成本。

程序测试通过与答案正确是不同结论。开发集用于诊断；正式结论需要证据核查、冻结测试、跨模型复核和不确定性分析。
