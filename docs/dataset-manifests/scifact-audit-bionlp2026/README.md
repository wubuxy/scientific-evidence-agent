# SciFact 标签审计参考资料

`dev_corrections.json` 和 `train_corrections.json` 转载自 Julien Sylvestre 的 [BioNLP 2026 配套仓库](https://github.com/Kefez/scifact-audit-bionlp2026/tree/5711f3fdf2305552df2b6e700dca2ebd7c8806e1)，固定提交为 `5711f3fdf2305552df2b6e700dca2ebd7c8806e1`。作者将这些勘误资料按 **CC BY 4.0** 发布。文件内容保持原样；本项目的分析和文档级范围解析另行记录。

论文：[Gold Label Errors in the SciFact Benchmark: An LLM-Assisted Annotation Audit](https://aclanthology.org/2026.bionlp-1.9.pdf)，Julien Sylvestre，BioNLP 2026，97–103。

这是单人结合 LLM 的审计建议，尚缺第二位独立人工复核者，不能称为 SciFact 原作者正式更新的 gold。`doc_id=null` 的普通条目涉及该声明的已标注证据文档，不能传播到所有被引但无标注的文档。

声明 597 是文档级例外，但源 JSON 没有填写 doc ID。诊断脚本的默认外部勘误情景暂不应用它；另一个明确标记的情景仅将 `official-dev-597-12779444` 视为证据不足，因为该摘要研究死亡率，而声明是发病率。另两篇摘要涉及发病率，不作同样更正。此范围判断仍待独立人工仲裁。
