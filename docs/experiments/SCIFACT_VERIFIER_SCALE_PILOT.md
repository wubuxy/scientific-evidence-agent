# A5000 核验器规模与精度对照协议

日期：2026-11-06。目标是在同一张 A5000 上，区分更大通用模型、量化精度和任务微调各自对应的效果，并加入公开专用核验器作为外部基线。沿用上一轮的训练与校准论文，正式测试只选未被上一轮预测或校准涉及的新论文连接组。QASPER 200 题保留集继续不使用。协议、代码、实际模型哈希和硬件预检结果在正式运行前提交；不根据测试成绩追加种子、提示或训练步数。

## 论文与模型依据

| 来源 | 已核对的实现 | 本轮用途 |
|---|---|---|
| [MiniCheck，EMNLP 2024](https://aclanthology.org/2024.emnlp-main.499/)及[后续 Bespoke-MiniCheck-7B 权重](https://huggingface.co/bespokelabs/Bespoke-MiniCheck-7B) | 原论文有 Flan-T5 等小模型；本轮用作者后续发布、基于 InternLM2.5-7B 的专用核验器。其名称中的 Llama-3.1 指合成数据生成模型，不是核验器架构。卡中说明训练包含 21k ANLI 与 14k 合成例 | 外部二分类基线，不称为原论文 770M 实验的复现 |
| [Lynx，2024](https://arxiv.org/abs/2407.08488)与[8B 权重](https://huggingface.co/PatronusAI/Llama-3-Patronus-Lynx-8B-Instruct) | Llama-3-8B 的 RAG 幻觉检测微调模型，输出 PASS/FAIL 与理由 | 已核对的候选，当前不扩展为第三套推理协议 |
| [ARES，NAACL 2024](https://aclanthology.org/2024.naacl-long.20/) | 使用 DeBERTa-v3-Large 等轻量评判器及人工标签校正，模型大并不是可靠评测的充分条件 | 保留人工校准与分任务评分要求，不宣称本轮实现 ARES/PPI |
| [PremiseCheck，2026-10 预印本](https://arxiv.org/html/2610.21046v1) | 论文内容事实核验；将评论拆成前提后核验，并比较 MiniCheck-7B 等模型 | 参考原子声明与错误类型设计，其成绩不能直接外推到本项目 |
| [Qwen2.5-7B-Instruct](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct)和 [QLoRA](https://arxiv.org/abs/2305.14314) | 同系列更大模型可减少新架构适配；4-bit 训练仅是显存手段，效果仍需对照 BF16 | 主训练对象与精度对照 |

MiniCheck 权重标注 CC BY-NC 4.0，本轮用于研究比较。各模型实际权重分片必须匹配官方 Hugging Face LFS SHA256；下载可用 ModelScope 镜像。MiniCheck 的自定义 Python 已按固定版本审阅，执行前核对代码 git blob 哈希，只从本地加载，不执行跨仓库 AutoModel 映射。模型、数据、缓存和结果均保存在 `D:\Ding`。

GPU 环境与应用环境分开，使用 Python 3.12、官方 cu118 索引的 torch 2.7.1，以及 [requirements-verifier-gpu.txt](../../backend/requirements-verifier-gpu.txt)。NLTK 的 `punkt_tab` 存在 `/mnt/d/Ding/cache/nltk`，通过 `NLTK_DATA` 指定；自定义模型代码缓存通过 `HF_MODULES_CACHE` 指向 D 盘。安装失败可使用清华或阿里 PyPI 镜像，不改变实际权重哈希。

## 实验条件

| 条件 | 模型与处理 | 可回答的问题 |
|---|---|---|
| small-bf16 | Qwen2.5-1.5B，BF16，无训练 | 小模型未量化基线 |
| small-nf4 | 同一 1.5B，NF4，无训练 | 小模型的量化敏感性 |
| large-bf16 | Qwen2.5-7B，BF16，无训练 | 同系列更大模型条件的表现 |
| large-nf4 | 同一 7B，NF4，无训练 | 7B 的量化损失 |
| large-lora × 3 | 同一 7B、三固定种子、LoRA | 相对于匹配精度未训练模型的训练收益 |
| minicheck-bf16 | Bespoke-MiniCheck-7B，BF16，不再训练 | 开源专用核验器的外部参照 |

所有 Qwen 条件使用上一轮完全相同的三类定义、问题/声明/证据输入和 A/B/C 单 token 判定。单条完整输入上限为 1,536 tokens，batch 1。MiniCheck 使用作者库的系统提示和 Document/Claim 格式，按 NLTK 拆分多句声明，逐句取第一 token 的 top-5 中 Yes 的完整词表概率之和，再取各句最小值；以严格大于 0.5 判支持。完整摘要不分块，上限 3,072 tokens。用 Transformers eager attention 执行同一权重与提示，属于单卡适配，不冒充官方 vLLM 的吞吐复现。

MiniCheck 输出 supported/unsupported，不强行映射成 contradicted/insufficient。所有模型共享“支持/不支持”二分类评测；三分类 macro F1 仅比较 Qwen。Qwen 的二分类决策由三分类 argmax 合并得到，连续 support score 是 A/B/C 内的归一化分数；MiniCheck 为原生 Yes 概率，两者不冒充同一种校准置信度。

## 数据与隔离

沿用 v1 的 535 对训练样本、65 对校准样本，且不改训练标签或模型可见内容。分组在原有连接键上增加论文 ID、忽略标题且转小写/合并空白的摘要哈希，以及同样归一化的 claim 哈希。官方 SciFact dev 按这些键的完整连接组处理；排除与 v1 已预测的 129 对、训练和校准输入有任一组键交集的整组。再按所有候选模型的完整 token 长度联合过滤，不截断原文；按固定散列顺序选满目标 128 对，并保留跨目标的整组。若不足 64 对或缺失某一类，则停止，不换成已观察论文补数。实际 ID、数量、分组与排除原因写入 plan。

正式测试输入与 gold 分文件。训练仅接收 training.json；推理仅打开 inputs，连 gold 哈希也不读取；独立 score 阶段才读取 test gold。公开 dev 可能进入基础模型预训练，本轮仅保证本项目内的论文和观测隔离，不称为无污染隐藏测试。16 条既有合成挑战例与 32 条移除证据诊断单独报告。

## 校准与硬件预检

预检先在最长的训练输入上做两个优化步，并保存/重新加载适配器，再对 3 条校准输入做预测。首选 BF16 LoRA；仅发生 CUDA 显存不足时，记录原失败，改为 NF4 QLoRA 做同样检查。两者均失败则停止。本轮不依据校准 F1 选择精度、提示或 checkpoint。随后对所有五个未训练配置执行同一 65 条校准输入，检查模型加载、原生输出和精度敏感性。预检结果单独保存并在正式运行前冻结。

基础 BF16 权重约需参数个数乘 2 bytes，但这不含激活、工作区和缓存，不能由权重大小保证训练可用。BF16 训练不调用会把非量化参数转换为 FP32 的 k-bit 准备函数。正式每个训练种子都从原始模型重新加载，使用硬件预检选择的同一精度。

## 训练预算

固定种子 `20261106 / 20261107 / 20261108`；LoRA rank 8、alpha 16、dropout 0.05、q_proj/v_proj；128 optimizer steps，micro-batch 1、累积 4，每种子实际看 512 对，未完整遍历 535 对。AdamW 学习率 2e-4、weight decay 0.01、clip 1，前 8 步 warmup，之后线性衰减；只监督下一类别 token，开启非重入 gradient checkpointing。不做早停、最佳种子或 checkpoint 选择。最后一步适配器保存后重新加载再测试。

正式运行总墙钟上限 3 小时，包含加载、四组通用基线、三次训练和全部推理，不含下载、准备和校准预检。未完成、失败的计划条件和样本保留在分母，不以已完成种子替代三种子均值。基础设施修复保留失败目录；改变输入、提示、模型、训练或判定的代码修订必须重新形成冻结计划，不覆盖原运行。

## 主要指标与判断

三项预定主要差值均为三分类 macro F1：

1. 规模条件：large-bf16 − small-bf16。
2. 量化：large-nf4 − large-bf16。
3. 训练：三种子 large-lora 平均 − 相同精度的 large 基线。

按正式测试论文/claim 连接组配对 bootstrap 5,000 次，报告点估计、逐项 95% 区间及三项 Bonferroni 校正的 98.333% 区间。种子不当独立论文样本；多模型模型卡成绩不作本项目基线成绩。规模条件还包含基础模型预训练细节差异，不能把差值全归因于参数数量。

额外报告各类 precision/recall、混淆矩阵、supported 精确率、非支持样本误判支持率、缺失数，以及所有方法的二分类 balanced accuracy / AUROC。AUROC 遇缺失预测时留空并报告缺失，而不删掉失败样本制造完整排名。延迟按 batch 1 的编码、传输和前向计量；训练记录 allocated/reserved 峰值，均不冒充整卡显存峰值。

候选绝对门槛：三个训练种子的 contradicted recall **各自至少 0.5**，平均 macro F1 至少 0.65，平均 supported precision 至少 0.8，平均非支持样本误判支持率不超过 0.2。训练成功还要求平均差至少 +0.03，且校正区间下界大于 0。这些只表示值得下一阶段校准，不授权生产自动裁决；真实报告仍需独立人工标注和完整 Agent 对照。无论达到门槛与否，完成固定条件、独立评分和错误分析后结束本轮，不追逐正结果。
