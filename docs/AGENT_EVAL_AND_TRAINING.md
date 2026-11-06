# Agent 自动评测与训练路线

调研日期：2026-11-05；训练进展更新于 2026-11-06。以下区分论文报告、对本项目的推断和已落地代码，未复现下述论文成绩。已完成 1.5B pilot 及 1.5B/7B 双精度、三种子 BF16 LoRA、MiniCheck 对照，无新增付费模型调用；结果与自动评测的工程验收分开记录。

## 结论

先补评测对错误的定位与重复稳定性，再选择要训练的模块。当前已接通冻结短答案、五组补读对照、工作台完整报告 Runtime、联合证据判定和双人标注校准。真实语义质量通过独立标注测量；没有标注时，程序只输出候选判断，不生成准确率结论。

推荐主线：可追溯证据 Agent → 分层自动评测 → 错误库 → 针对性改进 → 必要时训练小模块。训练的目标应是减少一类已确认错误或降低达到同等质量的成本。

## 参考资料与具体取舍

| 来源 | 文献或文档提供的启发 | 本项目下一步 |
|---|---|---|
| [Is Grep All You Need?](https://arxiv.org/html/2605.15184v1)，2026-05 | 检索表现受执行框架和工具结果交付方式影响 | 比较检索方式时保持同一循环与输出协议；完整报告另测，不能套用短答案成绩 |
| [HiEviDR-Bench](https://arxiv.org/html/2607.25151)，2026-07 | 从证据、声明到结论分层检查，并用门控定位失败阶段 | 分别记录地址有效、原文支持、问题覆盖；保留各阶段原始分和门控分，避免只剩一个总分 |
| [Anthropic Agent Evals](https://www.anthropic.com/research/demystifying-evals-for-ai-agents)，2026-01；[LangSmith 复杂 Agent 评测文档](https://docs.langchain.com/langsmith/evaluate-complex-agent) | 终态、过程与单步分别评测；重复运行检查可靠性 | 固定任务重复完整检索轨迹；过程规则允许多条合法路径，不要求完全复制某条示范轨迹 |
| [ARES](https://arxiv.org/html/2311.09476v2)，NAACL 2024 | 用合成数据训练轻量评判器，并用人工标注校正自动评估 | 可探索声明支持判定器；先有人工校准集，不能把大模型标签直接当真值。普通多数表决也不等同于 ARES 的 PPI |
| [Search-R1++ 研究](https://arxiv.org/html/2602.19526v1)，2026-02 | 在其设置中，提示、奖励和算法的选择都会改变结果，F1 奖励曾诱发退化 | 不直接把当前 token F1 当唯一 RL 奖励；同时检查弃答、证据、动作有效性和资源用量 |
| [SENTINEL](https://arxiv.org/html/2606.12908v1)，2026-06 | 从失败轨迹归纳弱点，再构造针对性训练任务 | 先借鉴错误驱动的数据构造；合成挑战集与自然任务集分开统计，不先引入整个 RL 流程 |
| [Agent Lightning](https://arxiv.org/abs/2508.03680)，2025-08 | 让 Agent 执行与训练基础设施解耦 | 日志先形成可复用的状态、工具动作、观察和结果记录，未来训练时保留既有工具边界 |

近期预印本尚需独立复核。尤其 SENTINEL 的工具任务结果不能直接外推到论文问答。以上表中的“下一步”是针对本项目的设计推断。

另外，[Coding Agents are Effective Long-Context Processors](https://arxiv.org/html/2603.20432v1) 与 [LangChain 2026-10 检索文档](https://www.langchain.com/blog/context-engineering-starts-with-retrieval) 支持继续研究文件导航、分页与结果指针。本项目可以先做确定性的章节行号目录、按缺失字段检索和结果段优先级；现有预定义工具尚不能等同于自由编程 Coding Agent。

[False Success 研究](https://arxiv.org/abs/2606.09863) 在它考察的任务中指出，完成声明与真实终态可能脱节。对本项目的对应要求是：`status=completed`、报告流畅、模型自评“有依据”都不能独立定义任务成功。

## 已实现：完整轨迹的重复评测

`backend/app/research_backend/benchmarks/trials.py` 是统一入口。短答案方法每次重新执行搜索、阅读和生成；`report-grep` / `report-rag` 调用工作台实际使用的 `research_backend.runtime.Runtime`，不把固定答案重放当作新的独立运行。报告和短答案必须使用不同计划，程序拒绝混合两种评分口径。

1. `plan` 固定输入 / manifest 哈希、任务 ID、代码哈希、模型、方法、重复次数、预算、参考方法和通过阈值，不调用模型。
2. `run` 启动互相隔离的完整试次。生成子进程不接收 gold 路径。任务失败不会被重新挑选；恢复执行只补尚未启动的试次，已存在试次保留。
3. `score` 在独立阶段读取 gold，所有计划中的失败、缺失试次都计入分母。
4. 每题先汇总重复结果；配对 F1 的 bootstrap 按共享论文的连接组抽样。跨论文题共享任一论文时仍属于相关组，不能把重复和相关题当独立样本。
5. 统计调用失败和完全重复的工具请求。它们是诊断观察，不能单凭计数就断言根因。

短答案的“通过”是预先固定的词面协议：每个目标都达到 token F1 阈值，回答时具有有效的已导出原文引用，且未观察到 token 超限。它不代表语义支持。代码保留 `lexical_` 命名；完整报告另报契约、字段存在、论文覆盖与候选语义判断。

当前重复可靠性同时报告：

- `pass_at_k`：观察到的试次中，抽取 k 次至少一次满足该词面协议的组合估计。
- `pass_all_k`：抽取 k 次全部满足该词面协议的组合估计，对应“每次都可靠”的关注点。
- 两者遵循 [τ-bench](https://arxiv.org/abs/2406.12045) 和 Anthropic 文档区分的使用目标；它们不是人工语义成功率，也不是把任务平均成功率简单乘方。

F1 置信区间与上述可靠性点估计分开报告。只有一个独立论文连接组时，不输出没有独立样本支撑的 bootstrap 区间。缓存被多个方法和试次共享，因此耗时不能冒充冷启动性能。

成本记录区分完整已知用量、部分已知用量和未知总用量。模型异常 / 缺 usage 时总量为 null，已有成功调用的用量仍保留。无法完整计量时不输出一个看似完整的平均成本；embedding 和评判调用也应分别记账。

### 使用命令

先按 [BENCHMARKS.md](BENCHMARKS.md) 准备冻结数据，并在 `backend` 设置 `$env:PYTHONPATH='app'`。下面的 ID 文件必须是预先选定的唯一任务 ID 数组。

```powershell
python -m research_backend.benchmarks.trials plan --inputs ../benchmark-data/qasper-v1/dev/inputs.json --task-ids-file ../task_ids.json --methods grep rag-once --reference grep --repeats 3 --success-f1 1.0 --out ../study-plan.json
python -m research_backend.benchmarks.trials run --plan ../study-plan.json --inputs ../benchmark-data/qasper-v1/dev/inputs.json --out ../benchmark-runs/repeated-agent
python -m research_backend.benchmarks.trials score --inputs ../benchmark-data/qasper-v1/dev/inputs.json --gold ../benchmark-data/qasper-v1/dev/gold.json --run ../benchmark-runs/repeated-agent --out ../benchmark-runs/repeated-agent/analysis.json
```

第一条命令不产生模型费用。第二条会使用已配置模型服务；计划会给出名义 LLM token 总上限，不包含 embedding、评判及无法获取的服务端计费。`--env-file` 可交给执行命令。若进程中断，可用相同 plan 与 `--resume` 继续未启动的试次；已开始的试次不会因失败而被重跑。

计划文件本身不等同于独立预注册。应在运行前提交计划和代码，保留时间戳与哈希；之后不依据分数更改题目、阈值或分母。早期 dev50 已用 36 题、35 篇论文；后续五组实验又从同一池固定 20 题，存在重叠，不能沿用“还剩 14 道未观察题”的说法。新的确认实验应另建开发样本并排除所有已用论文，200 题保留测试集继续留到方法冻结后使用。

初版用合成离线 fixture 验证编排与统计。此后已经完成真实模型的 20 题、两次上游、五组 200 结果实验，主比较未确认优势，见[正式记录](results/REPAIR_FIVE_ARM_DEV20_V1.md)。端到端软件验收脚本仍使用本地假模型 HTTP 服务，通过实际 CLI 检查两轮五组对照、两次完整报告、联合证据判定和标注校准；软件验收不计入真实模型效果规模。

第一版的 10 项编排检查保留。后续增加 11 项完整链路检查，覆盖生产 Runtime、五组隔离、实际与逻辑成本、联合引用、空标注拒绝、分歧仲裁、判定缺失分母和真实 CLI。完整套件结果见 [REPRODUCIBILITY.md](REPRODUCIBILITY.md)，测试数量只表示软件验证规模。

### 完整报告、联合证据与人工校准命令

完整报告使用独立计划和目录。`report-rag` 会使用已配置的 embedding 服务并产生额外费用。每次运行导出真实报告、当前回读窗口、模型公共消息、工具事件和已测量用量；网络发现关闭，工作区隔离。

```powershell
python -m research_backend.benchmarks.trials plan --inputs ../benchmark-data/qasper-v1/dev/inputs.json --task-ids-file ../task_ids.json --methods report-grep --repeats 3 --out ../report-plan.json
python -m research_backend.benchmarks.trials run --plan ../report-plan.json --inputs ../benchmark-data/qasper-v1/dev/inputs.json --out ../benchmark-runs/repeated-report
python -m research_backend.benchmarks.reports export --inputs ../benchmark-data/qasper-v1/dev/inputs.json --run ../benchmark-runs/repeated-report --out ../benchmark-runs/report-annotations
python -m research_backend.benchmarks.claim_labels judge --cases ../benchmark-runs/report-annotations/cases.json --out ../benchmark-runs/report-reviews.json --max-tokens 20000
python -m research_backend.benchmarks.trials score --inputs ../benchmark-data/qasper-v1/dev/inputs.json --gold ../benchmark-data/qasper-v1/dev/gold.json --run ../benchmark-runs/repeated-report --reviews ../benchmark-runs/report-reviews.json --out ../benchmark-runs/report-reviewed-scores.json
```

导出会生成协调者使用的 `cases.json`、人工阅读用的 `human-cases.json`、`annotator-a.json`、`annotator-b.json` 和 `adjudication.json`。人工阅读版按不透明 ID 排序，仅保留问题、声明及证据；方法、轮次与候选标签不随人工包分发。每人只接收阅读版和自己的标签模板，具体见[双人标注协议](HUMAN_REVIEW_PROTOCOL.md)。声明和表格的每个已填写事实单元分别评判；跨论文声明将全部所引证据一起交给判定器，不能选一个最高分的引用代替联合判断。自定义表格字段不会覆盖标准字段的检查。`field_presence_rate` 仅统计请求字段是否填写；对自由文本中隐含的字段不做语义推断，不能当成回答充分性。

两位标注者分别填写各自文件的 `verdict`，使用不同的 `annotator` 标识；分歧在第三个文件中仲裁。保留 `cases_sha256`，不要把模型候选意见复制成独立人工标签。完成后运行：

```powershell
python -m research_backend.benchmarks.claim_labels calibrate --cases ../benchmark-runs/report-annotations/cases.json --reviews ../benchmark-runs/report-reviews.json --labels-a ../benchmark-runs/report-annotations/annotator-a.json --labels-b ../benchmark-runs/report-annotations/annotator-b.json --adjudication ../benchmark-runs/report-annotations/adjudication.json --out ../benchmark-runs/report-calibration.json
```

校准输出 Cohen's kappa、各类别 precision / recall / F1、宏 F1、无依据声明被误判 supported 的比例和判定覆盖率。空标签、未仲裁分歧、变更后的证据、重复判断和伪造证据跨度会被拒绝。缺失或失败的判定保留在校准分母；所有标签同类导致 kappa 无定义时返回 null。人工一致性与判定器准确率分别报告。没有人工校准时，报告的 `semantic_success_rate` 保留 null，`candidate_supported_rate` 明确只是模型候选结果。

无需真实凭据的完整验收，在 `backend` 运行，要求安装核心依赖和 ripgrep：

```powershell
python ../scripts/smoke_agent_evals.py --out test-results/agent-evals-smoke
```

输出目录必须尚不存在。脚本中生成的标注显式标记为 `synthetic_fixture`，不会冒充人工标注或真实质量实验。

## 完整报告自动评测应分四层

| 层次 | 可自动执行的内容 | 仍需补齐的部分 |
|---|---|---|
| 程序契约 | 引用归属、本次回读、原文逐字一致、格式、工具权限、终止和预算 | 这些通过只说明程序约束满足 |
| 任务结果 | 按要求覆盖论文和字段；有参考答案的短答案 F1；正确弃答与错误弃答分别统计 | 长报告需要字段参考与原子声明标注，不能把 completed 当成功 |
| 语义支持 | 对 claim 与所引证据检查 supported / contradicted / insufficient，另测回答充分性、方法角色、实验阶段 | 自动判定器先与人工标签校准；跨源结论需要联合证据，不能仅逐引用打分后取最高分 |
| 可靠性与资源 | 重复运行、配对差、论文分组区间、token / 调用 / 耗时与未知用量覆盖率 | 更换模型、任务分布或环境后重新检查；小样本不保证显著性 |

不宜只让另一个模型给报告打 1–10 分。先将声明拆成原子事实，再对每个事实关联原文和可回答性，最后聚合。地址无效时记为地址问题，证据未读到时记为证据问题；已读到仍答错需要进一步核查生成与评分，不用规则强行猜根因。

CI 分层：每次改代码跑不付费的契约与 fixture 回归；模型配置或检索策略变化后手动运行小型真实模型对照；语义评判器升级时用固定人工校准集检查。当前没有新增自动消耗模型额度的定时任务。

## 下一项受控研究：核验是否比普通补搜更有效

研究问题固定为：在相同总预算上限内，依据证据缺口定向补读，是否优于仅重答、关键词兜底和普通额外搜索。先用共享上游轨迹的分支回放定位机制，再用重新搜索、阅读、生成的完整试次确认；两类结果分开报告。

| 条件 | 固定的处理方式 | 回答的问题 |
|---|---|---|
| A | 原始 Grep Agent | 现有基线表现如何 |
| B | 核验后仅重新作答，不补读 | 增益是否来自第二次生成 |
| C | 核验后按缺失字段定向补读，再作答 | 证据缺口驱动是否有效 |
| D | 不核验，使用问题关键词执行一次兜底搜索和回读 | 简单规则能否达到同样效果 |
| E | 不核验，允许模型进行一次普通额外搜索和回读 | 增益是否只来自多一次搜索机会 |

各条件固定相同模型、原文、终止规则和每次任务的总 token 上限；核验、查询生成、补读后作答均计入该上限。相同上限不等于相同实际开销，因此同时报告实耗、未知用量和质量—成本关系，不能只报 F1。离线评分器的费用另列，不混入执行策略预算。D/E 的触发条件、可用状态、搜索和回读额度必须在运行前写定，不能看完 C 的成功案例再设计对照。

先在开发数据上做小规模重复诊断，确定错误分类和人工评分协议；随后另选未用于调参的新论文做确认。20 道题、3 次重复可以作为诊断规模的起点，不保证能够检出 5 个百分点的差异；确认规模应结合 pilot 的论文间方差与可承担成本决定。主要结果应预先选定，区间与成本门槛也要在确认前冻结，避免从多个指标里事后挑正结果。

五条件已由 `repair_arms.py` 接入统一执行器。创建计划时使用 `--methods repair-baseline repair-rewrite repair-reread repair-keyword repair-search --reference repair-baseline`。每个试次先重新运行一个 Grep 上游，最多使用总 token 预算的 60%，再从同一轨迹产生五个配对分支；因此 A 是该共享上游基线。完整预算的普通 `grep` 可以作为独立方法同时加入，但不等于共享上游 A。

```powershell
python -m research_backend.benchmarks.trials plan --inputs ../benchmark-data/qasper-v1/dev/inputs.json --task-ids-file ../task_ids.json --methods repair-baseline repair-rewrite repair-reread repair-keyword repair-search --reference repair-baseline --repeats 3 --out ../repair-plan.json
python -m research_backend.benchmarks.trials run --plan ../repair-plan.json --inputs ../benchmark-data/qasper-v1/dev/inputs.json --out ../benchmark-runs/repeated-repair
python -m research_backend.benchmarks.trials score --inputs ../benchmark-data/qasper-v1/dev/inputs.json --gold ../benchmark-data/qasper-v1/dev/gold.json --run ../benchmark-runs/repeated-repair --out ../benchmark-runs/repeated-repair/analysis.json
```

B/C 共享一次核验，只有核验提示需要修复时才重新作答；C 至多再搜索一次、回读 40 行。D 在每个已完成且计费已知的上游执行问题关键词兜底；E 从问题、原始回答与已读证据提出一次普通搜索，D/E 不接收核验结果。所有 writer 使用相同的原始证据字符上限。补读失败或预算不足时按固定策略保留原答案，同时保留成本和错误，不能静默删除。当前研究范围明确限定单论文、单目标任务，计划阶段拒绝不符合范围的数据。

每个策略独立计入其应承担的上游与核验成本；`usage.json` 的 `measured_llm_tokens` 对物理共享调用只记一次，`logical_policy_tokens` 用于各策略独立运行时的比较。共享回放的耗时不标成独立端到端延迟；在线分支的时间预算按每条逻辑策略而非所有分支串行的总耗时执行。真实五组实验与事后缓存回放的边界、失败和结果见[实验报告](results/REPAIR_FIVE_ARM_DEV20_V1.md)。

## 是否微调，以及训练什么

### 候选一：声明支持与充分性判定器

**已执行的最小实验：** 按运行前冻结的协议，使用 SciFact 专家标签完成论文隔离的 535 对训练、65 对校准和 129 对测试，运行三种子 QLoRA。平均 macro F1 0.212→0.501，但 NF4 基线塌缩，微调模型的反驳召回仅 0–5.3%，暂不接入自动裁决。这个 pilot 仅预测指定摘要下的类别，不输出充分性或证据跨度；详细证据与后续约束见[结果报告](results/SCIFACT_VERIFIER_PILOT_V1.md)。下列真实报告人工标注要求仍需独立满足。

**第二轮已执行：** 另选 128 对、103 组新测试，完成 1.5B/7B BF16/NF4、三种子 7B BF16 LoRA 及 MiniCheck。7B BF16 macro F1 为 0.581，LoRA 平均 0.746，校正区间支持局部训练收益；但支持精确率平均降到 76.0%，且一个种子的反驳召回低于 50%，未过候选绝对门槛。MiniCheck 支持精确率 91.9%，只作二分类参照。单卡实际训练、失败留痕和完整复算见[规模对照结果](results/SCIFACT_VERIFIER_SCALE_V2.md)。下一阶段应先降低假支持并做真实 NLP 声明人工校准，不由总分提升直接推导自动评测可靠。

**第三轮已执行：** 在已观察评估集上进行原始标签与 7 条公开更正建议的六次训练对照，生成 1,446 条新预测。原始口径平均 F1 0.748→0.743，错误支持率 15.64%→15.23%；公开建议口径也未观察到稳定收益，主要区间均跨 0。不能把这一探索性结果当作经过人工仲裁的数据清洗实验；下一步仍优先人工复核和目标域自然声明测试，见[标签干预结果](results/SCIFACT_LABEL_ABLATION_V1.md)。

可训练，但先建立论文隔离的人工审阅集。输入是问题、一个原子声明及其已读证据；输出包括支持类别、回答充分性、角色 / 阶段是否对齐，以及可解析的证据行号。简单引用归属与逐字匹配继续由程序完成。

先比较规则、未训练小模型提示、大模型候选判定三种基线，再决定是否给小模型做 SFT / LoRA。合成训练样例可通过改变数字、否定、方法归属和训练 / 评估阶段构造；合成标签需要过滤，不与人工确认集混用。训练、校准、测试按论文划分，保持类别和自然任务分布可见。

关注 macro F1、把无依据声明误判 supported 的比例、supported 精确率、自动判定覆盖率、延迟和成本。只提高 supported 精确率却把所有声明都弃判不算完整成功；三类标签的一致率也不能替代准确率。ARES 是这一方向的有用先例，不能据其论文结果认定本项目的小判定器一定有效。

### 候选二：下一步动作与查询生成

如果诊断表明主要损失来自没有读到相关原文，应优先检查导航、检索和补读策略。用 `(问题、已读证据、上次工具结果、剩余预算)` 预测 `(搜索 / 阅读 / 停止、参数)`，比直接训练整个报告生成器更容易限定目标。

先加强低成本对照：章节导航、方法 / 结果段检索、按缺失字段重写查询。模型动作可以做 SFT，数据来自经核验的训练论文轨迹；参考答案可用于筛选训练数据，但不能进入生成时状态，更不能用保留测试答案筛选轨迹。比较同一个基础模型的未训练和训练版本，以完整任务质量与成本评估，不只看工具参数格式命中率。

### 当前不优先：整个 27B 生成器与端到端 RL

现有推理 API 接入不等于已经搭好可训练权重、训练服务和算力。先确定可训练的小模型及许可、显存和部署方式；[PEFT LoRA](https://huggingface.co/docs/peft/en/package_reference/lora) 与 [量化训练指南](https://huggingface.co/docs/peft/en/developer_guides/quantization) 可作为实现依据。是否采用 4-bit LoRA，要按模型、序列长度与硬件实测。

等人工校准、动作数据和可靠奖励具备后，再考虑 DPO 或 Agent RL。Search-R1++ 对其设置中的奖励退化提供了提醒；不能由论文推导“某种算法在本项目必然最好”。Agent Lightning 的执行 / 训练解耦可以保留为后续接入方向。

## 下一轮优先级

1. 小型五组重复对照已完成，主比较未确认优势；停止在同一 20 题上追加策略来追求正结果，保留全部失败、逐题差和成本。
2. 收集真实报告的原子声明，分别标注支持、充分性、角色与阶段；先校准评判器。
3. 根据主要错误，选择补强低成本搜索策略或训练一个小判定器，不同时扩张多个方法。
4. 方法与通过标准冻结后，在新论文上做确认；最后使用保留测试集。没有达到标准时，保留 null 结果和错误分析。
