# Agent 架构策略：先隔离变量，再决定是否采用

这些是可独立关闭的开发实验开关，生产默认值保持不变。目标是处理已经观察到的重复回读、限定条件被截断和报告格式失败。它们不替代引用校验，也不把字段填满视为事实正确。

| 开关 | 默认 | 实验行为 | 可能的代价 |
| --- | --- | --- | --- |
| `read_guidance` | `false` | 合并已读行区间，在命中附近给出合法的 40–60 行候选，显示重复行数；按论文平衡 | 导航本身占用上下文；仍允许为恢复遗漏条件重新读取 |
| `memory_priority` | `recent` | `read_first` 优先保留实际回读，搜索片段不能覆盖同 ID 的可引用原文；只适用于 `memory_mode=evidence` | 较新的搜索线索可能被挤出 |
| `writer_context` | `prefix` | `lexical` 在每个已读窗口中用本地 BM25 选两段八行原文，保留实际行号和原始 evidence ID | 词面相关不等于支持；可能漏掉跨段关系；仅在最终写作交接时生效 |
| `report_schema` | `full` | `compact` 从模型输出协议移除六个非必需通用列，用指定的 `attributes` 表达本题内容；保存时恢复完整报告默认值 | 减少了通用列的提示作用，可能改变内容组织 |

每个开关都写入 run 记录。组合版同时启用四项；组合增益不能归因于某一单项。`legacy` 仍是默认记忆模式，结构化记忆作为本轮实验共同基线。

## 保留的约束

- 只有当前运行真正回读的证据可被引用，完整原文及工具轨迹仍持久化。
- 原文摘录不重写成摘要；选择器不读取 gold、外部全文或未回读内容。
- 字段要求、引用归属、推断限定条件及方案依据校验不放松。
- 计划字段必须使用原名；缺项保留，模型核验只是候选意见。
- 所有条件共享总 token、步数和时间上限。实际请求数和 token 可以不同，这是实验结果的一部分。

## 与现有工作的关系

[PaperQA2](https://arxiv.org/abs/2409.13740) 的证据收集与回答分离、[Anthropic 的上下文工程文档](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) 的按需取回思路，为架构拆分提供参考。本仓库的确定性窗口选择和读区间导航是小规模工程假设，不是这些系统或训练方法的复现。

本轮固定使用两篇已见公开论文、两类开发问题、六条件和每条件两次重复，共 24 次尝试。没有动用 QASPER 200 题留出集。问题和论文存在依赖，样本量不足以宣称显著提升。完整预注册在 [strategy-sweep-dev-v1/plan.json](results/strategy-sweep-dev-v1/plan.json)。

```powershell
# 准备仅限新实验，已经冻结的目录不能覆盖
backend/.venv-ci-analysis/Scripts/python.exe scripts/strategy_sweep.py prepare
# 运行使用已有 provider 环境文件；密钥不进入实验档案
backend/.venv-ci-analysis/Scripts/python.exe scripts/strategy_sweep.py run --env-file PATH_TO_ENV
backend/.venv-ci-analysis/Scripts/python.exe scripts/strategy_sweep.py archive
# 无模型调用：重新核对原文、引用、用量、配置和所有产物哈希
backend/.venv-ci-analysis/Scripts/python.exe scripts/strategy_sweep.py audit
```

人工标签未返回前，语义准确率始终为 `null`。自动结果只回答能否交付、字段是否存在、读了什么和花了多少；是否值得采用，还需要两名独立评审及新论文确认。
