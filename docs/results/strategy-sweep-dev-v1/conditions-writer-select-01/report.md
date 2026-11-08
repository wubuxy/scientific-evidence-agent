# QASPER 与 ArxivDIGESTables 训练评测做法迁移性比较报告

状态：待研究者审核

- QASPER 数据集构建采用解耦流程：研究生撰写问题，另一组经过资格考核的标注者基于全文回答并选择最小证据片段；系统输入为检索到的上下文伪文档列表。 [ev_dbd51bfe615cf2f61578502434256dc7, ev_41a8045d162faf52b65c54038c9b52cd, ev_89d62505e66c7db455184fe28318360d]
- ArxivDIGESTables 数据集由从论文中解析出的参考表格构成，过滤了字符数、引用数和行列数不满足条件的表格；部分表格值（如实验结果）无法仅从输入文本推导。 [ev_9aeb9f5f73e6ac8a1f44fb6ae5e00ac4, ev_2643df162aa82f062f26bef562f74a68]
- QASPER 评测指标为 Answer-F1 和 Evidence-F1，基于系统选择的段落/图表/表格与参考证据的匹配；人类性能估计为 Answer-F1 60.9、Evidence-F1 71.6，此为下限。 [ev_e4c799ac6be5608f64e32b3748e1742a]
- ArxivDIGESTables 评测包含 schema 对齐（DECONTEXT EVAL）与单元格语义等价判断；模型生成时为避免刷高 recall，被指令限制生成与参考相同数量的方面。 [ev_9e30f13e93d36b6f1cfce039c02b3d73, ev_4bec3941768b23e1f3c68c2b46194953]
- QASPER 基线模型使用 LED-base（基于 BART 初始化），支持长文档（16K tokens）；ArxivDIGESTables 实验使用了 Mixtral 8x22 和 GPT-3.5-Turbo，但未说明是否微调。 [ev_741fd24f5360d351968ae598608b289a, ev_9e30f13e93d36b6f1cfce039c02b3d73]
- [推断] 两篇论文的评测条件不同：QASPER 依赖检索上下文且存在多证据参考，ArxivDIGESTables 依赖表格结构对齐与额外上下文（如生成的 caption）；直接跨任务排名不可比。 [ev_89d62505e66c7db455184fe28318360d, ev_2643df162aa82f062f26bef562f74a68, ev_9e30f13e93d36b6f1cfce039c02b3d73]
  适用范围：基于两文对输入上下文、参考形式及指标定义的差异推断

| 论文 / 方法 | 角色与输入 | 训练与数据划分 | 评测条件与指标 | 迁移条件与未知 | 证据 |
|---|---|---|---|---|---|
| LED-base (BART-initialized) | 问题作者 vs 标注者；系统输入为检索伪文档 | 未明确微调细节；使用预训练权重 | F1 over paragraphs/figures/tables; oracle-based | 需验证检索质量对 F1 的影响 | ev_741fd24f5360d351968ae598608b289a, ev_e4c799ac6be5608f64e32b3748e1742a, ev_dbd51bfe615cf2f61578502434256dc7 |
| Mixtral 8x22, GPT-3.5-Turbo | 无显式标注者；输入为论文文本+可选上下文 | 未说明微调；可能为零样本/少样本推理 | DECONTEXT EVAL for schema; non-oracle cell check | 需验证额外上下文对 schema 重建的贡献 | ev_9e30f13e93d36b6f1cfce039c02b3d73, ev_2643df162aa82f062f26bef562f74a68, ev_9aeb9f5f73e6ac8a1f44fb6ae5e00ac4 |

## 方案建议（尚未执行，待验证）

- 在 QASPER 子集上对比零样本推理与微调后的 LED-base 在 Answer-F1 上的差异，固定检索器与上下文长度。
  依据：结论 1, 5
  假设：微调能提升复杂推理任务的 F1；检索质量是主要瓶颈而非模型容量
  验证计划：选取 500 个问答对，分别用零样本和微调模型测试，记录 F1 变化；若提升<2%则停止微调尝试
- 在 ArxivDIGESTables 上测试添加不同上下文（caption、示例表）对 schema 对齐率的影响，固定模型为 GPT-3.5-Turbo。
  依据：结论 2, 6
  假设：额外上下文能显著提升 schema 重建能力；模型对提示敏感
  验证计划：设计三组提示（无上下文、仅 caption、caption+示例），各测 200 表，统计 schema 对齐率；若差异不显著则放弃该方向

## 限制与待验证问题

- QASPER 未明确说明模型是否经过微调，仅提及使用预训练权重初始化
- ArxivDIGESTables 未提供具体数值结果，仅描述评测方法
- 两文评测指标定义不同，无法直接进行跨任务性能排名
- 部分表格值无法从输入文本推导，影响非 oracle 条件下的评估有效性