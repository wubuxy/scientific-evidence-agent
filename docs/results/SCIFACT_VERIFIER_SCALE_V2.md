# A5000 上的 7B 核验器规模与精度实验

完成日期：2026-11-06。**7B BF16 LoRA 已在这张 A5000 上实际跑通；更大底座和任务微调在本地测试上都有明确收益，但微调后的支持精确率仍不足以通过预定候选门槛。**

后续[数据与误判诊断](VERIFIER_DATA_DIAGNOSIS.md)发现本批样本涉及公开标签更正建议，并报告了固定预测的敏感性分析。本文继续保留原始 SciFact gold 下的冻结成绩；更正建议尚待独立人工仲裁，下一轮计划见[数据对照方案](../experiments/VERIFIER_DATA_EXPERIMENT_PLAN.md)。

在同一批 128 对声明—摘要上，Qwen2.5-1.5B BF16 的三分类 macro F1 为 **0.281**，7B BF16 为 **0.581**，7B LoRA 三种子平均为 **0.746**。专用核验器 MiniCheck 的二分类支持精确率为 **91.9%**，但不能直接承担“矛盾/证据不足”的三分类判断，空证据诊断也有错误。所有模型均保留为研究组件，没有接入生产自动裁决。

## 目标、依据与冻结记录

本轮分别检查同系列更大模型的表现、同一权重的 NF4 量化影响、同精度任务微调的增益，并引入外部专用核验器。选用作者发布的 [Bespoke-MiniCheck-7B](https://huggingface.co/bespokelabs/Bespoke-MiniCheck-7B)，参考 [MiniCheck，EMNLP 2024](https://aclanthology.org/2024.emnlp-main.499/)；它基于 InternLM2.5-7B，模型名称中的 Llama-3.1 指合成数据生成模型。原论文的 770M 模型与这个后续发布的 7B 模型分开说明，不把本轮结果冒充原论文复现。

[Lynx 8B](https://huggingface.co/PatronusAI/Llama-3-Patronus-Lynx-8B-Instruct)也是已核对的开放权重幻觉检测候选，本轮未运行。ARES 的轻量评判器和近期 PremiseCheck 的论文前提核验用于指导任务取舍，详见[运行前协议及参考文献](../experiments/SCIFACT_VERIFIER_SCALE_PILOT.md)。Qwen 7B 与 1.5B 属于同系列，但预训练细节并非严格相同，规模条件差值不能全部归因于参数个数。

测试 ID 在 `decee25` 提交固定。修复后的运行使用代码 `e52ae95`，实际模型哈希、硬件预检和校准结果于 **`b267574` 提交冻结**，之后开始完整生成。首次中断运行的原冻结清单、部分预测与失败分母另行保留。[修复后冻结清单](../dataset-manifests/scifact-verifier-scale-v2-corrected.json) · [原冻结清单](../dataset-manifests/scifact-verifier-scale-v2.json)

## 数据与比较口径

| 项目 | 实际范围 |
|---|---|
| 训练 | 沿用 SciFact 535 对 / 289 个连接组，标签与模型可见内容沿用 v1 |
| 校准 | 沿用 65 对 / 36 组，不依据分数选择提示、精度或 checkpoint |
| 新测试 | **128 对 / 103 个论文与 claim 连接组**：47 supported、33 contradicted、48 insufficient |
| 隔离 | 排除与训练、校准及 v1 已观察 129 对有连接的整组；额外按论文 ID、忽略标题/大小写/空白的摘要及 claim 去重 |
| 长度 | Qwen 完整输入 ≤1,536 tokens；MiniCheck ≤3,072 tokens；209 对新候选中 207 对联合适配，排除两条长输入后按固定散列取样 |
| 额外诊断 | 16 条既有合成挑战例、从测试输入移除证据的 32 例，单独报告 |

训练/校准/测试的分组交集均为 0，测试清单与提前发布的 ID 完全一致。每个种子固定 128 个 optimizer steps、batch 1、梯度累积 4，实际看 512 对，未完整遍历 535 对。LoRA rank 8、q_proj/v_proj，仅 **2,523,136** 个适配器参数可训练；7B 底座冻结，不是全量参数微调。三个种子为 20261106 / 20261107 / 20261108，不按分数挑选。

本地测试来自 SciFact **公开 dev**，不称为隐藏或无预训练污染测试。输入已经指定被引摘要，不包含检索、工具循环或全文报告。生成程序不打开测试 gold；评分独立进行。QASPER 的 200 题保留集未使用，云模型调用为 0。

## 三分类结果

每个模型完成全部 128 条测试预测。均值是三个已完成种子的算术平均，不是挑选最佳种子。

| 条件 | Macro F1 | Contradicted recall | Supported precision | 非支持样本误判支持率 |
|---|---:|---:|---:|---:|
| 训练多数类 supported | 0.179 | 0.0% | 36.7% | 100.0% |
| 1.5B BF16 | 0.281 | 21.2% | 42.5% | 75.3% |
| 1.5B NF4 | 0.181 | 0.0% | 37.3% | 97.5% |
| 7B BF16 | 0.581 | 15.2% | 80.0% | 11.1% |
| 7B NF4 | 0.569 | 15.2% | 77.8% | 12.3% |
| 7B LoRA 20261106 | 0.743 | 57.6%（19/33） | 74.1% | 17.3% |
| 7B LoRA 20261107 | 0.747 | 51.5%（17/33） | 74.1% | 17.3% |
| 7B LoRA 20261108 | 0.748 | 48.5%（16/33） | 80.0% | 12.3% |
| **7B LoRA 三种子均值** | **0.746** | **52.5%** | **76.0%** | **15.6%** |

三项预定差值按 103 个论文/claim 连接组配对 bootstrap 5,000 次。下表为百分点，区间是三项 Bonferroni 校正后的逐项 **98.333% 区间**；普通 95% 区间也在 JSON 中保留。

| 预定比较 | Macro F1 差 | 校正区间 |
|---|---:|---:|
| 7B BF16 − 1.5B BF16 | **+30.05 pp** | **[+20.21, +40.35]** |
| 7B NF4 − 7B BF16 | −1.25 pp | [−3.84, 0.00] |
| 7B LoRA 三种子均值 − 7B BF16 | **+16.46 pp** | **[+7.67, +26.02]** |

更大模型条件及微调增益在这组固定比较下的区间不含 0；7B 量化差值区间包含 0。不能由 1.5B 的退化断言 4-bit 总会破坏核验，也不能把这里的增益外推成完整 Agent 幻觉率下降。区间反映当前测试连接组对这三个已观察种子平均值的波动，不是对所有未来训练种子的总体保证。

## 外部专用核验器与安全性取舍

MiniCheck 原生输出 supported/unsupported，因此只参与所有模型共同的二分类比较。Qwen 的类别由三分类 argmax 合并；连续分数为 A/B/C 内的支持分数。MiniCheck 使用原生 Yes 概率与阈值，两者不称为同一种已校准置信度。二分类结果是原生决策策略的比较，不能把 MiniCheck 的 unsupported 强行当作 contradicted。

| 条件 | 二分类 balanced accuracy | AUROC | 支持精确率 | 支持召回率 | 非支持误判支持率 |
|---|---:|---:|---:|---:|---:|
| 7B BF16 | 82.7% | 0.894 | 80.0% | 76.6% | 11.1% |
| 7B LoRA 三种子均值 | 84.7% | 0.916 | 76.0% | 85.1% | 15.6% |
| **MiniCheck BF16** | **84.3%** | **0.938** | **91.9%** | **72.3%** | **3.7%（3/81）** |

MiniCheck 在本集的支持精确率、AUROC 和误判支持率点估计更好，召回较低；balanced accuracy 并没有优于所有微调种子。这些次要指标没有预注册的差值区间，不能据点估计声称统计显著领先。

**本轮微调主要改善了“矛盾/不足”的区分，同时放行了更多错误支持。** 三分类 macro F1 提高 16.46 pp，但支持精确率从 80.0% 降到平均 76.0%，非支持误判支持率从 11.1% 升到 15.6%。二分类 accuracy 从 84.4% 到平均 84.6%，没有同等幅度的增长。高总分不能替代对错误支持的约束。

预定绝对候选门槛要求每个种子的矛盾召回 ≥50%、平均支持精确率 ≥80%、平均 macro F1 ≥0.65、平均误判支持率 ≤20%。第三个种子的矛盾召回为 16/33，且平均支持精确率未达标。因此 **`absolute_candidate_gate=false`、`training_success_gate=false`**，尽管相对增益和配对完整性通过。该门槛没有在看完分数后降低。

## 额外诊断与失败例

| 诊断 | 结果 |
|---|---|
| 16 条合成挑战的三分类 macro F1 | 7B BF16 0.458；三个 LoRA 均为 0.822；仅属合成诊断，不是真实报告准确率 |
| 16 条合成挑战的二分类 balanced accuracy | 7B BF16 与各 LoRA 为 87.5%；MiniCheck 93.8% |
| 32 条空证据诊断 | 七个 Qwen 条件均未误判 supported；**MiniCheck 有 2/32 条误判 supported** |

MiniCheck 的本轮原生结果保留空证据错误。实际集成应先由程序拒绝空证据，不能因其总体精确率较高就让它独立批准声明。项目现有引用归属与本次回读约束仍由程序执行。

[失败案例](scifact-verifier-scale-v2/failure-cases.json)按冻结测试顺序取每类最先出现的至多 3 条，包含所有模型误判支持、所有训练种子漏识矛盾、训练退化和训练修复矛盾四类。比如 `official-dev-759-1805641` 与 `official-dev-1207-18909530` 被八个模型同时错误接受；`official-dev-72-6076903` 从 BF16 基线的正确不足判断变成三种子均错误支持。对模型一致错误仍按官方 gold 计错，不用模型共识改写标签；这些是事后描述例，不证明内部错误机制。

## 硬件、耗时与中断记录

使用 Windows 主机上的 Ubuntu / WSL2、Python 3.12.3、驱动 537.13，GPU 报告容量 23,028 MiB。Torch 2.7.1/cu118、Transformers 4.51.3、PEFT 0.15.2；完整版本见 [gpu-packages.txt](scifact-verifier-scale-v2/gpu-packages.txt)。

| 种子 | 训练与保存 | PyTorch peak allocated | PyTorch peak reserved |
|---|---:|---:|---:|
| 20261106 | 213.9 秒 | 15,237 MiB | 17,968 MiB |
| 20261107 | 214.1 秒 | 15,237 MiB | 17,968 MiB |
| 20261108 | 213.8 秒 | 15,227 MiB | 17,968 MiB |

两种显存指标均不等于整卡占用；训练耗时不含底座加载、重载和评估。修复后完整模型执行命令耗时 **22 分 34 秒**，不含下载、安装、预检和独立评分。batch 1 的测试平均编码/传输/前向耗时为 1.5B BF16 35.1 ms、7B BF16 121.6 ms、7B NF4 170.4 ms、各 LoRA 约 131 ms、MiniCheck 150.0 ms；不包括加载或 Agent 检索，也不冒充官方 vLLM 吞吐。

第一次正式运行在第二个种子变慢时被中断。Python 3.12 的 `locals()` 帧字典保留上一底座引用，导致跨种子同时持有模型。修复改为显式清空句柄并添加生命周期回归；后续种子恢复到约 214 秒/128 步。详见[资源修复记录](../experiments/VERIFIER_SCALE_RESOURCE_FIX.md)。

旧版本保留 **1,205/1,928 条**预测，缺失条件计入分母，其训练差值为 **−32.86 pp**、成功门槛为 false；[独立归档](scifact-verifier-scale-v2/interrupted-run-001/analysis.json)可复算。修复后的完整版本为 1,928/1,928 条，没有拼接两次运行中的最好条件。中断版本的评分在修复后的完整生成结束后才执行，资源修复不依据测试效果。

两次运行已完成条件的 1,205 条共同记录中，1,203 条标签一致；不同的两条均来自同一个已训练种子的测试预测。相同种子的适配器权重哈希也不完全相同，本轮未启用强制确定性训练，**不承诺逐位重现**。[一致性记录](scifact-verifier-scale-v2/rerun-consistency.json)保留观察结果，不为差异臆造已验证的原因。

## 复查、复现与保存位置

无需下载权重或安装 torch，在仓库根目录运行：

```bash
python scripts/audit_verifier_scale.py --bootstrap
python scripts/audit_verifier_scale.py --root docs/results/scifact-verifier-scale-v2/interrupted-run-001 --bootstrap
```

审计检查冻结 plan、原始预测及 gold 哈希、重复/未知 ID、失败分母，并用独立的混淆计数、成对 AUROC 和 bootstrap 实现复算全部点估计与主要区间。两版本均已通过。CI 对三种后端环境执行数据审计，并在完整环境复算校正区间；软件通过与研究门槛通过分开报告。

工程验证为本机完整环境 **177 passed**；服务器 Python 3.12 核心环境 **164 passed, 13 skipped**。测试验证程序行为，不把 177 项软件测试当成 177 道模型质量题。1,928 条记录是 8 配置 ×（65 校准 + 128 测试 + 16 合成 + 32 空证据），不增加原有 96 次完整 Agent 上游运行数。

服务端模型、缓存、数据和结果均在用户指定的 `D:\Ding`：

```text
models/Qwen2.5-7B-Instruct/                         7B 原始权重
models/Bespoke-MiniCheck-7B/                        专用核验器权重
data/scifact/verifier-scale-v2-003/                 修复后冻结输入、gold 和 plan
results/verifier-scale-pilot-v2/run-002/             三个适配器、损失、预测与协议
results/verifier-scale-pilot-v2/run-001/             原中断运行，不覆盖
results/verifier-scale-pilot-v2/analysis-002.json    完整版本评分
```

模型训练代码按 `e52ae95` 冻结，研究复现应使用该版本及记录的环境；结果审计不要求未来应用源码永久不变。已配置服务器上，在仓库 `backend` 下可另建尚不存在的输出目录重跑：

```bash
source /home/unix/.config/scientific-evidence/env.sh
export PYTHONPATH=app
GPU_PY=/home/unix/.venvs/scientific-evidence-gpu/bin/python
PLAN=/mnt/d/Ding/data/scifact/verifier-scale-v2-003
OUT=/mnt/d/Ding/results/verifier-scale-pilot-v2/run-REPRO
"$GPU_PY" -m research_backend.benchmarks.verifier_scale run --plan-dir "$PLAN" --preflight /mnt/d/Ding/results/verifier-scale-pilot-v2/preflight-003/preflight.json --out "$OUT"
"$GPU_PY" -m research_backend.benchmarks.verifier_scale score --plan-dir "$PLAN" --run "$OUT" --out "$OUT/analysis.json"
```

本轮选择 7B BF16 作为后续三分类研究底座，MiniCheck 作为二分类参照。下一阶段的重点是降低假支持、保持反驳召回，并在真实 NLP 报告声明上独立标注校准；当前测试集已经观察过，不能继续在其上调配置后称为新的确认结果。
