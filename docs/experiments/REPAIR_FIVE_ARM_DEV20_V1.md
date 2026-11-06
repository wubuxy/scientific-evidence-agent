# 五组补读策略的重复开发实验

日期：2026-11-06。运行前方案；任务、代码和预算由同目录 `repair-five-arm-dev20-v1/plan.json` 固定。这是已观察开发池上的探索实验，不能作为新论文泛化确认。200 题 QASPER 保留测试不参与本轮。

## 问题和对照

检验核验驱动的补读，是否比同预算下普通额外搜索更有效。

| 条件 | 方法 | 与其他组共享的部分 |
|---|---|---|
| A | 原始 Grep 上游答案 | 同题同次的上游搜索与阅读 |
| B | 核验后仅重答 | 上游与 B/C 共享核验 |
| C | 核验后定向搜索和补读再答 | 同上 |
| D | 使用问题关键词补搜、回读再答 | 上游；不接收核验结果 |
| E | 模型提出普通额外查询、回读再答 | 上游；不接收核验结果 |

主比较为 C−E。C−D、C−B 和各组相对 A 作为机制诊断，均报告，不从中挑选最有利的比较作为主结果。A 只使用共享上游的预算，不代表完整 20,000 token 的普通 Grep 上限。

## 数据与执行

恢复原来冻结的 QASPER dev50，逐字节核对 inputs/gold 哈希。按 `SHA256("repair-dev20-v1:20261106:" + task_id)` 升序选前 20 题，得到 19 个论文连接组；选样不读取答案或历史得分。

固定 `qwen3.5-27b`、temperature 0.1、`enable_thinking=false`。每题重复 2 次，重新生成上游轨迹，共 40 条上游、200 个逻辑策略结果。每条策略累计输入与输出上限 20,000 token；上游最多占 60%，B/C 的核验、D/E 的查询和所有重答均计入各自预算。最多 10 个上游步骤，每条逻辑策略 300 秒，并发 2。五组最多补搜一次、补读 40 行；具体触发与失败回退沿用已测试的 `repair_arms.POLICY`。

名义逻辑 token 上限为 4,000,000。物理共享调用只计费一次，单独报告实际物理用量与各策略独立部署时的逻辑用量。连接检查使用非研究内容，已消耗 310 token，单独记账；本轮不需要 embedding 或外部模型评分。

主模型使用已配置的百炼 API；A5000 保存数据与完整实验档案，本轮不训练本地核验器，避免同时改变模型、数据与 Agent 策略。运行目录使用新目录，固定失败保留，不因低分或失败替换题目或选择性重跑。

## 指标和判断规则

主指标是官方参考下的答案 token F1，每题先平均两次，再对题平均。C−E 的 95% 配对 bootstrap 区间按 19 个论文连接组重采样，5,000 次、seed=20261106。重复和共享论文的题不作为独立样本。

进入更大新论文确认实验的探索门槛为：C−E 至少 +5 个百分点，95% 区间下界大于 0，且 C 的平均逻辑 token 不超过 E 的 1.2 倍。成本不完整时不判定成本门槛通过。此门槛只用于决定后续资源投入，不将开发集上的通过解释为确认性发现。

同时报告逐题差、全部失败、引用地址有效率、弃答与任一参考一致率、段落读取召回、词面 pass-at-k / pass-all-k、调用数和成本覆盖率。token F1 与引用地址有效均不等于语义支持；没有双人人工标签，不报告真实语义成功率。候选声明支持率不参与本轮成功标准。

结果为 null 或退化时按原分母公开结果，检查未检索到、补读无新增证据、核验误触发、正确改错及预算不足等轨迹现象，不追加重复来追求显著性。任何事后切片标为探索性，修订后的方法另建计划。

## 复现

从仓库根目录，在已安装科研依赖的环境设置 `PYTHONPATH=backend/app`：

```powershell
python -m research_backend.benchmarks.trials run --plan docs/experiments/repair-five-arm-dev20-v1/plan.json --inputs benchmark-data/qasper-repair-v1/dev/inputs.json --out benchmark-runs/repair-five-arm-dev20-v1 --env-file path/to/config.env
python -m research_backend.benchmarks.trials score --inputs benchmark-data/qasper-repair-v1/dev/inputs.json --gold benchmark-data/qasper-repair-v1/dev/gold.json --run benchmark-runs/repair-five-arm-dev20-v1 --out benchmark-runs/repair-five-arm-dev20-v1/analysis.json
```

生成命令不接收 gold。公开计划和逐题指标；正文、完整回读轨迹、配置与凭据不提交到 Git。
