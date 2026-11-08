# Deep Research 阅读路线：从搜索到可验收的研究

整理日期：2026-11-07。下面只引用原论文、作者项目或官方工程文档。论文结果是其固定模型、工具、预算和评分器下的结果，不能直接换算成本项目表现；2026 年 arXiv 工作按预印本/技术报告阅读。

## 先建立领域地图

针对本次“预算不足是否来自上下文”的故障，优先阅读 [Anthropic 上下文工程](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)、下文的 Sufficient Context 与 RARR。具体诊断、采用的机制和未证明的部分见 [上下文与契约修复](results/CONTEXT_REPAIR_V1.md)；阅读论文后先修执行问题，再验证质量。

Deep Research 通常包含问题拆解、反复获取资料、管理工作记忆、综合并引用证据、检查缺口和停止。它覆盖网页事实查找、长报告和企业资料调研；这些任务的难度与成功标准不同。综述 [Deep Research: A Systematic Survey](https://arxiv.org/abs/2512.02038) 将规划、信息获取、记忆和回答组织为四个核心组件，并梳理提示、SFT 和 Agent RL。建议先读组件图和评测章节，再按下面的问题阅读。

| 研究主线 | 大家在做什么 | 仍未解决的关键问题 |
|---|---|---|
| 搜索与规划 | 分解问题，交替搜索、浏览、写作，必要时重新规划 | 分解遗漏、检索重复、网页噪声与工具故障 |
| 多 Agent 与记忆 | 主 Agent 分派独立子问题；压缩轨迹、保留外部工作记录 | 协调成本、摘要丢失限定条件、证据来源失真 |
| 训练搜索策略 | 用真实搜索轨迹、合成任务、SFT/RL 改进信息获取 | 奖励是否代表报告价值，训练环境迁移，rollout 成本 |
| 报告与验收 | 细粒度要求、引用支持、信息覆盖和分析质量分别评分 | 写得完整不等于事实准确，核验器也会犯错 |
| 停止与修复 | 根据证据缺口选择补搜、重写或结束 | 错误宣称完成、过早弃答、越改越错、额外成本 |

这张表是本项目对下列资料的归纳，不是某一篇论文的结论。

## 第一轮：先读这六篇

### 1. STORM：研究之前需要问什么？

[Assisting in Writing Wikipedia-like Articles From Scratch with Large Language Models — NAACL 2024](https://aclanthology.org/2024.naacl-long.347/)

通过不同视角的提问与有来源的交流收集资料，再生成大纲。重点读 pre-writing 阶段和大纲评测：长文章的质量不仅依赖最后写作，也依赖有没有提出足够好的问题。本项目可借鉴要求清单和缺口发现；多视角产出仍需要查证，不能把角色数量视为质量证据。

### 2. WebThinker：边找边想边写

[WebThinker: Empowering Large Reasoning Models with Deep Research Capability](https://arxiv.org/abs/2504.21776) · [NeurIPS 2025 论文](https://papers.nips.cc/paper_files/paper/2025/file/ae03bdef276132fae089692445725635-Paper-Conference.pdf)

把网页探索和研究报告写作放入推理过程，并通过迭代在线 DPO 改善工具使用。读工具接口、搜索与写作怎样交替、训练与纯提示的对照。它说明可以训练动作策略；并不意味着先给任意项目做微调就会获益。我们先需要知道错误来自获取资料还是理解资料。

### 3. Anthropic Research：系统工程怎样支撑研究？

[How we built our multi-agent research system — 2025-06-13](https://www.anthropic.com/engineering/multi-agent-research-system)

官方工程文章，采用主 Agent 与并行研究子 Agent 的结构。重点看任务边界、工具设计、子任务返回什么以及工程可靠性。可以借鉴清晰的任务交接与执行记录；这是产品团队的经验报告，不是所有任务都应使用多 Agent 的受控证明。本项目的单篇定点补读暂不需要多 Agent。

### 4. DeepResearch Bench II：究竟怎样算研究做得好？

[DeepResearch Bench II: Diagnosing Deep Research Agents via Rubrics from Expert Report — 2026](https://arxiv.org/abs/2601.08536)

132 个任务、22 个领域、9,430 条细粒度标准，分别考察信息召回、分析和表达，标准来自专家报告并经过人工审核。重点读 rubric 构建、人工一致性和漏项分析。论文发布时最强系统通过不足一半标准，这是该论文实验快照，不能描述为今天所有系统的上限。我们借鉴“逐项要求验收”，不向执行 Agent 提供隐藏参考答案。

### 5. Sufficient Context：该补检索还是改答案？

[Sufficient Context: A New Lens on Retrieval Augmented Generation Systems — ICLR 2025](https://arxiv.org/abs/2411.06037)

区分上下文不够回答与模型没有正确利用已有上下文，研究充分性与选择性作答。重点看错误分类和弃答取舍。我们应分别记录证据支持和回答完整性；只找到了相关词，不能证明问题已经解决。该研究的模型/数据结论不能直接外推为当前核验器的准确率。

### 6. HALT：证据覆盖能否决定何时停止？

[HALT: Verification-Aware Stopping for Retrieval-Augmented Search Agents — 2026-08 预印本](https://arxiv.org/html/2608.02009v1)

逐个检查预期推理环节的证据，比较至少一个环节满足与全部环节满足的停止策略。核验器使用 Qwen2.5-3B-Instruct 的 LoRA；目标来自问题生成或 gold，后者仅是诊断上界。重点读 generated-claim 条件、核验成本和质量非劣效检验。我们借鉴按要求覆盖来控制停止，但保留新证据推翻旧判断的可能，不能把状态做成只能升级。

## 第二轮：训练、修复与评测补充

| 阅读 | 主要贡献 | 阅读时追问 |
|---|---|---|
| [DeepResearcher，2025](https://arxiv.org/abs/2504.03160) | 在真实网页交互环境训练研究策略 | 训练奖励、工具环境、失败轨迹和费用是什么？网页 QA 的提升如何迁移到长报告？ |
| [Tongyi DeepResearch Technical Report，2025](https://arxiv.org/abs/2510.24701) | Agent 中期/后期训练与自动数据合成；模型总参数 30.5B，每 token 激活 3.3B | 激活参数不等于权重显存；论文的训练资源、量化推理和我们单卡条件应分开判断。 |
| [DuMate-DeepResearch，2026 技术报告](https://arxiv.org/abs/2606.07299) | 动态任务图、递归搜索、基于任务标准的推理与停止 | 标准由谁生成？是否会遗漏用户要求？论文报告的榜单领先仅对应其比较条件。 |
| [RARR，ACL 2023](https://aclanthology.org/2023.acl-long.910/) | 查找依据并修改无支持内容，同时尽量保留原输出 | 如何测量原来正确的内容被改坏？我们的修复应只影响有问题的字段。 |
| [Evidence-Carrying Termination，2026-08 预印本](https://arxiv.org/abs/2608.23623) | 完成声明绑定执行证据，确定性重放检查结果 | 合成/可重放工具任务的保证不能延伸成自然语言语义真值。可借鉴版本、来源和验收记录。 |
| [TASR，2026-06 预印本](https://arxiv.org/html/2606.13814v3) | 用答案稳定性及校准后的概率信号停止迭代检索 | 依赖 token logprobs、短答案等条件；长报告迁移尚未验证。“不再改变答案”不等于正确。 |

## 评测地图：不要把不同排行榜当成同一种能力

| 基准 / 文档 | 主要衡量什么 | 对本项目的价值 |
|---|---|---|
| [BrowseComp，2025](https://arxiv.org/abs/2504.12516) | 难定位但有简短可验证答案的浏览问题 | 衡量资料发现，不能单独代表长报告分析能力 |
| [Deep Research Bench / FutureSearch，2025](https://arxiv.org/abs/2506.06287) | 89 个多步网页研究实例；冻结网页的 RetroSearch | 借鉴环境冻结与轨迹诊断，减少网页变化干扰 |
| [DeepResearch Bench / Du 等，2025](https://arxiv.org/abs/2506.11763) | 100 个任务、22 个领域；报告质量与引用质量 | 与上一行是不同工作；参考报告评分与引用检查需要分开 |
| [DeepResearch Bench II，2026](https://arxiv.org/abs/2601.08536) | 专家报告导出的细粒度信息、分析和表达标准 | 为遗漏字段、正确性和分析设置具体要求 |
| [DRBench，2025](https://arxiv.org/abs/2510.00172) | 15 个企业研究任务，覆盖公开网页与私有公司资料 | 资料权限、来源混杂和企业背景，超出当前本地论文实验范围 |
| [Anthropic Agent evals，2026](https://www.anthropic.com/research/demystifying-evals-for-ai-agents) | 区分任务、试次、轨迹、终态；组合程序、模型和人工评判 | 报告生成成功与要求满足分别记账，模型评判需用人工校准 |

## 大家做到什么地步了？

上述工作已展示多步浏览、长报告写作、并行研究和通过训练改善工具策略；当前竞争也在转向覆盖、分析、成本与终止可靠性。它们没有给出一个适用于所有研究问题的“已完成”判据。

阅读成绩时先核对模型、检索环境、时间/token 上限、任务范围和评分器。官方 [DRB 统一榜单](https://muset-ai-deepresearch-bench-leaderboard.hf.space/?__theme=system) 在本次访问时包含不同评分器的独立视图，DRB2 视图还存在 130/132 个计分任务的口径区别。不能把不同视图或论文历史成绩直接拼接排名；本指南不摘取一个脱离配置的“当前第一”。

本项目判断，最值得继续做的缺口有：用户要求能否完整保留；证据是否足够；停止是否过早；修复是否损坏正确内容；上下文压缩是否丢失限定；核验是否与人工一致；所有改进是否值得其额外成本。这些是研究问题，而不是已证实的本项目优势。

## 对我们的实现意味着什么

先实现可审计的字段状态：固定要求 → 搜索/回读 → 候选答案 → 程序检查 → 核验意见 → 有限修复或带缺口交付。模型只提供候选，控制器保存版本与证据，不能用模型自报 `done` 作为真值。保留原有行为作对照，不把历史实验结果套到新执行器。

92 条自然声明的人工标注首先校准语义支持；字段充分性与整个任务的遗漏需要另建标准。先观察状态和补救的实际行为，再在新论文上做同预算对照。当前无需为字段状态训练模型；未来训练是否有价值，应由错误分布、校准结果和强基线比较决定。

推荐阅读顺序：**STORM → WebThinker → Anthropic 工程文章 → DRB II → Sufficient Context → HALT**。每篇记录四件事：Agent 可选的动作、完成判据、反馈来源、实验中不能证明什么。
