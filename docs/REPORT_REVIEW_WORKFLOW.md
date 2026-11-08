# 从真实报告到两人独立评审

本流程评估完整研究报告，和此前 92 条自然声明的 claim-level 标注任务分开。两者不能混合计算准确率。

## 标注者怎么做

1. 分别领取 `annotator-a.zip`、`annotator-b.zip`，解压后打开 `review.html`。无需联网或模型密钥。
2. 填写各自的评审者代号。逐份阅读问题、报告和本次所读原文；每份报告有六条预先冻结的标准。
3. 每条标准全部满足才给 **1**，不满足给 **0**，不能判断保留未判断。写明理由，最好注明 evidence ID 和行号。表格填满、引用存在、语言流畅均不等于正确。
4. 暂停或完成时点击“导出 labels.json”。浏览器本地记录只是便利，导出的文件才用于交付。两人的导出文件分开保存，不互相查看或交流答案。
5. 可以打开包内论文全文核查，但不能把报告没有引用的原文补算成它已提供的引用支持。

所有初始分数为空。包内不包含实验条件、模型自评或费用。`coordinator-mapping.json` 只给协调者，不能分发给标注者。未交付报告不会伪装成可评分报告，仍留在总尝试分母中。

## 协调者怎么处理

```powershell
python scripts/package_architecture_review.py --root docs/results/strategy-sweep-dev-v1 --out test-results/strategy-review
python scripts/score_architecture_review.py --packet-dir test-results/strategy-review --a annotator-a-labels.json --b annotator-b-labels.json --out test-results/review-agreement.json
```

导入器核对原始包哈希、匿名报告和标准 ID、独立标注者代号、0/1 分数及理由；显式提供的 evidence_spans 必须落在该报告的实际回读范围。评分文件中的独立性和人工来源仍是标注者声明，并非身份认证。

输出包括每人填写数、缺失数、配对一致率、Cohen’s kappa、按标准的一致性及待仲裁列表。评分缺失不补零；无配对标签、或 kappa 分母为零时保留 `null`。不同意见不自动投票或取平均变成真值。按条件汇总的评分均值只描述已收到的标签，并单独列出失败和缺失分母。

一份报告里的六项标准、同论文上的问题和重复运行不是独立样本，因此当前不做显著性检验。只有独立标注、分歧仲裁及必要的新论文确认完成后，才能讨论质量收益；双人一致不等于客观正确。

## 离线检查页面

页面通过真实浏览器检查：空标签默认值、分数与理由提示、切换报告、刷新恢复、JSON 导出和手机宽度下的布局。测试使用明确标记的合成输入，不进入人工标签。

```powershell
# 可选开发工具环境：Playwright 与本机 Chrome
python scripts/check_report_review_page.py --page PATH_TO_EXTRACTED_PACKET/review.html --out test-results/review-browser-check
```

页面没有外部脚本或网络请求；论文与工具输出按文本显示，不执行其中的 HTML。
