# 示例论文与历史记录

两篇论文的 arXiv 页面均链接到 **CC BY 4.0**：

- Pradeep Dasigi, Kyle Lo, Iz Beltagy, Arman Cohan, Noah A. Smith, Matt Gardner. *A Dataset of Information-Seeking Questions and Answers Anchored in Research Papers* (2021). https://arxiv.org/abs/2105.03011v1
- Benjamin Newman, Yoonjoo Lee, Aakanksha Naik, Pao Siangliulue, Raymond Fok, Juho Kim, Daniel S. Weld, Joseph Chee Chang, Kyle Lo. *ArxivDIGESTables: Synthesizing Scientific Literature into Tables using Language Models* (2024). https://arxiv.org/abs/2410.22360v1

许可：https://creativecommons.org/licenses/by/4.0/ 。原作者保留署名与著作权；本项目不暗示作者背书。

`papers/*.txt` 为既有工作区中 PDF 提取后的文本，包含提取造成的断行、连字和版面损失；不代表原始 PDF 版式。`*.blocks.json` 保留导入时的页码与块信息。未添加或改写论文内容，转换为 JSON / TXT 是格式变化。行号只对应本演示文本版本。

`recording.json` 来自 2026-10-28 的真实工作台运行，模型为 `qwen-plus`，不是当前默认模型 `qwen3.5-27b` 的新评测。保留原始问题、结论、限制声明、工具调用 ID、回读 evidence ID 和来源版本；只导出本演示需要的公共论文内容与工具地址，不包含模型内部推理、密钥或私人工作区内容。

失败定位保留原始报告的错误声明，事后核对的窗口标记 `post_run_manual_review`，不伪装为原运行已经读过或自动修复的证据。核验补读数字来自 2026-11-05 另一组 6 题离线实验的已存评分摘要，不是上述工作台运行的分支，也不是当前页面生成的新结果。

来源版本 `source_hash` 对论文块（包含页码等元数据）的规范 JSON 求 SHA-256；`content_sha256` 对导出 TXT 的 UTF-8 内容求 SHA-256。两者的对象不同。
# 字段接通回放

`field-recording.json` 为本项目自编虚构文本上的真实接通记录。Atlas、Train-X、Seed-A 和 Eval-Y 均非真实研究结果；无第三方论文正文、人工标签或真实盲审判断。模型意见只对应这条合成记录，不是准确率真值。来源与导出方法见 [`docs/SHOWCASE.md`](../../../docs/SHOWCASE.md)。
