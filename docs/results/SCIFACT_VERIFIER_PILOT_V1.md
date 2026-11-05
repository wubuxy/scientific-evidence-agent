# A5000 核验器 pilot v1：链路完成，暂不用于自动裁决

完成日期：2026-11-06。**训练链路可用；这轮没有得到可靠的三分类核验器。** 在固定 129 对声明—摘要测试输入上，三个 QLoRA 种子的平均 macro F1 为 **0.501**，原始 NF4 模型为 **0.212**。原模型把全部测试输入判成 supported；微调模型对 contradicted 的召回仅 **0%、5.3%、0%**。因此不能把平均涨幅直接表述为“可靠核验能力提升”，也没有据此接入生产 Agent。

## 固定设计与实际执行

[协议](../experiments/SCIFACT_VERIFIER_PILOT.md)与代码先提交于 `320ad58`；[划分与哈希清单](../dataset-manifests/scifact-verifier-pilot-v1.json)在正式运行前提交于 `4c36c8f`。本页是事后结果说明，没有修改冻结协议、样本、提示、训练步数或主要指标。

| 项目 | 实际配置 |
|---|---|
| 硬件与系统 | RTX A5000，驱动报告 23,028 MiB；Windows 主机上的 Ubuntu 24.04 / WSL2 |
| 模型 | Qwen2.5-1.5B-Instruct；同一基础权重、NF4 双重量化、BF16 计算 |
| 数据 | SciFact 官方 train 划分训练/校准；官方公开 dev 的一部分作为本地测试，不称隐藏测试 |
| 划分 | 训练 535 对 / 289 连接组；校准 65 对 / 36 组；测试 129 对 / 90 组 |
| 测试标签 | supported 60、contradicted 19、insufficient 50 |
| 隔离 | 共享论文、重复 claim 和重复文本的连接组隔离；独立审计的论文 ID、摘要、claim 跨划分交集均为 0 |
| 训练 | 三个固定种子各 128 optimizer steps，batch 1 × 累积 4；每种子实际看 512 对，未遍历全部 535 对 |
| LoRA | rank 8、alpha 16、q_proj/v_proj，共 1,089,536 个可训练参数；仅监督类别 token |
| 输出 | 下一 token 在 A/B/C 中取 argmax；不是自由 JSON、证据跨度或充分性的联合预测 |

输入是已经指定的被引摘要，不包含检索任务；不提供 gold rationale。三个超过 1,536-token 上限的官方 dev 输入在预测前排除，未截断证据。项目的 16 条合成挑战例独立报告；QASPER 200 题保留集未使用。本轮没有云模型 API 调用。

## 正式测试结果

所有方法均完成 129/129 条预测，没有缺失或失败。多数类由训练标签决定，为 supported；恰好与本轮原模型预测一致。

| 方法 | Macro F1 | Accuracy | Supported precision | Contradicted recall | 非支持样本误判支持率 |
|---|---:|---:|---:|---:|---:|
| 多数类 / 原始 NF4 | 0.212 | 46.5% | 46.5% | 0.0% | 100.0% |
| QLoRA 20261105 | 0.490 | 68.2% | 70.0% | 0.0% | 30.4% |
| QLoRA 20261106 | 0.519 | 68.2% | 65.0% | 5.3% | 40.6% |
| QLoRA 20261107 | 0.493 | 68.2% | 69.1% | 0.0% | 30.4% |

三种子平均 macro F1 差为 **+0.289**，按 90 个共享论文/claim 连接组进行 5,000 次配对 bootstrap，95% 区间 **[+0.220, +0.352]**。这是对这套固定提示和量化基线的局部差值区间，不涵盖提示选择、模型预训练污染、不同模型或不同领域带来的不确定性；三个种子不是三倍的独立测试样本。

冻结的数值门槛 `pilot_quality_gate=true`：平均差至少 0.03、区间下界大于 0、误判支持率增量不超过 0.02。**数值门槛通过，部署结论仍为不通过。** 原门槛没有设 contradicted 最低召回，也没有防止基线塌缩；现在补充这一判断只能作为事后解释，不能篡改预注册的 gate 值。

| 额外诊断 | 原模型 | 三个 QLoRA 种子 |
|---|---:|---:|
| 16 条合成挑战例 macro F1 | 0.389 | 0.359 / 0.463 / 0.420 |
| 32 条移除证据输入的 insufficient 数 | 32/32 | 均为 32/32 |

一个种子在合成挑战例上退化；空证据能弃判也不代表能识别“有关联但不支持”的真实摘要。三类完整混淆矩阵见 [analysis.json](scifact-verifier-pilot-v1/analysis.json)。

## 事后诊断：基线为什么需要谨慎解释

正式测试中，原模型的 A/B/C 概率质量均值为 99.966%，最小 99.555%，排除了“候选字母概率都很低、解码器硬选出来”的简单解释。

随后**只使用 65 条校准输入**，按同一提示比较 NF4 与 BF16 原模型；没有重跑、重写或替换正式测试基线。两者均未训练，校准结果为：

| 精度 | 输出分布 | 校准 macro F1 |
|---|---|---:|
| NF4 | 65 supported | 0.169 |
| BF16 | 50 supported、15 contradicted、0 insufficient | 0.253 |

对前 12 条校准输入进行不限制 A/B/C 的贪心自由生成，NF4 均返回 A；BF16 有一条返回 B，与其类别预测一致。说明该配置存在精度敏感性，自由生成也能复现 NF4 的支持偏向；BF16 本身仍然很弱。现有证据不能把全部正式测试增益归因为语义学习，也不能定量分离量化恢复和任务适配的贡献。

诊断脚本为 [diagnose_verifier_calibration.py](../experiments/diagnose_verifier_calibration.py)，原始输出、逐例预测与独立评分保存在 [诊断记录](scifact-verifier-pilot-v1/calibration-baseline-diagnostic.json)。该记录的类别指标在生成后单独读取校准标签计算；生成脚本不读取任何评估 gold。

失败案例按“所有四个模型均把 contradicted 误判 supported”的条件，取测试输入顺序中的前 3 条，未选择最好看的案例。两条涉及作用方向，一条涉及复合声明中的方向变化；仅按文本进行描述，不声称已证明内部因果。见 [case ID、原始声明与预测](scifact-verifier-pilot-v1/failure-cases.json)。

## 资源、验证与保存位置

| 种子 | 训练耗时 | PyTorch peak allocated |
|---|---:|---:|
| 20261105 | 256.5 秒 | 2,172.6 MiB |
| 20261106 | 258.6 秒 | 2,174.2 MiB |
| 20261107 | 259.6 秒 | 2,174.2 MiB |

上表包含训练与适配器保存，不含基础模型加载、重新加载与评估。显存是 PyTorch 分配峰值，**不是整卡峰值或 reserved 显存**。测试按 batch 4 摊销的输入编码、传输和前向耗时：原模型 41.4 ms/例，LoRA 约 46.8–46.9 ms/例；不含模型加载、序列化、Agent 检索或自由文本生成，不能写成在线端到端延迟。

三个适配器分别保存后重新加载再预测。首次 smoke 因缺少 C 编译器失败；安装 `build-essential python3-dev` 后，新目录中的 smoke 和正式运行完成，原失败保留在 [smoke-history.json](scifact-verifier-pilot-v1/smoke-history.json)。没有依据测试结果挑选种子或继续加训。

服务端数据、模型和结果均在用户指定的 **`D:\Ding`**，WSL 对应 `/mnt/d/Ding`：

```text
Ding/models/Qwen2.5-1.5B-Instruct/                 原始模型
Ding/data/scifact/raw/                            官方压缩包与下载哈希
Ding/data/scifact/verifier-pilot-v1/              冻结输入、gold、训练样本和 plan
Ding/results/verifier-pilot-v1/run-001/            原始预测、适配器、训练损失与运行协议
Ding/results/verifier-pilot-v1/                   评分、校准诊断、失败和环境日志
Ding/results/verifier-pilot-v1/published-report/  随最终提交保存的可审阅副本
```

工程验证：本机完整环境 **168 passed**；服务器 Ubuntu 核心环境 **155 passed, 13 skipped**，均收集 168 项。新增的 5 项检查验证划分、gold 隔离和失败分母，**不验证模型质量**。原始 968 条正式预测记录由 4 个模型配置 ×（65 校准 + 129 测试 + 16 挑战 + 32 空证据）构成，不是 968 道独立任务，也不增加原有 96 次 Agent 上游运行数。

## 复查与重跑

无需 GPU、模型或下载，在仓库根目录运行：

```bash
python scripts/audit_verifier_pilot.py
```

该脚本检查冻结源码/协议哈希、原始预测哈希、重复与缺失记录，并用另一段标准库实现重新计算四组测试 macro F1。CI 的后端作业也执行这一审计。完整预测见 [predictions.jsonl](scifact-verifier-pilot-v1/predictions.jsonl)，仅含评分用 ID/组/标签的摘要见 [scoring-cases.json](scifact-verifier-pilot-v1/scoring-cases.json)；全文按冻结计划从官方语料重建。

已配置的服务器上，在 `backend` 下执行以下命令即可从同一冻结数据另建一次运行；输出目录必须不存在：

```bash
source /home/unix/.config/scientific-evidence/env.sh
export PYTHONPATH=app
GPU_PY=/home/unix/.venvs/scientific-evidence-gpu/bin/python
PLAN=/mnt/d/Ding/data/scifact/verifier-pilot-v1
OUT=/mnt/d/Ding/results/verifier-pilot-v1/run-REPRO
"$GPU_PY" -m research_backend.benchmarks.verifier_pilot run --plan-dir "$PLAN" --out "$OUT"
"$GPU_PY" -m research_backend.benchmarks.verifier_pilot score --plan-dir "$PLAN" --run "$OUT" --out "$OUT/analysis.json"
```

新机器环境：Python 3.12、系统 `python3-venv build-essential python3-dev`；PyTorch `2.7.1` 从官方 cu118 索引安装；Transformers `4.51.3`、PEFT `0.15.2`、Accelerate `1.6.0`、bitsandbytes `0.45.5`。完整安装版本见 [gpu-packages.txt](scifact-verifier-pilot-v1/gpu-packages.txt)。PyPI 下载使用清华源，模型取 Qwen 官方 ModelScope 镜像，数据取 SciFact 官方地址；实际文件哈希以运行前清单为准。通用核心/分析依赖与 GPU 训练依赖使用不同虚拟环境，未升级 Windows 驱动。

## 本轮结论与后续方向

可以在面试中说：**“我在 A5000 上完成了论文隔离、三种子、可重载的轻量核验器实验，并通过分类诊断发现量化基线塌缩和反驳类失效，因此没有把总分上涨包装成可靠核验，也没有让它自动裁决报告。”**

下一轮优先在训练/校准数据上建立能够区分三类的非塌缩基线，检查精度、提示与反驳样本覆盖，并在运行前加上各类召回和误判支持率的绝对门槛；随后用新论文做一次冻结确认。若这些条件尚不满足，继续保留人工复核。当前 129 对已经观察过，不能在其上反复选配置后继续称为确认测试；SciFact 的生物医学摘要结果也不能直接外推到 NLP 全文报告或完整 Agent 幻觉率。
