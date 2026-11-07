# 展示素材与公开回放

README 封面为手写 SVG 概念图；动图与静态图片来自真实 React 页面。合成论文中的 Atlas、Train-X、Seed-A 均为虚构名称。该记录来自已执行的 `qwen3.5-27b` 接通检查，5 次调用、15,814 tokens；本次制作回放没有新模型调用。

## 回放的来源

- 页面：`/demo/fields`，静态读取 `frontend/public/demo/field-recording.json`，不调用研究 API。
- 原记录：`scripts/smoke_field_state.py` 产生的合成接通运行，详见[原验收报告](results/FIELD_STATE_V1.md)。
- 导出器：`scripts/export_field_demo.py` 只接受指定的合成工作区和虚构来源，白名单导出字段、原文、版本与成本，拒绝普通研究工作区。
- 完整性：`backend/tests/test_demo_recording.py` 检查重新导入的来源哈希、原文行号、候选版本绑定，以及与已发表接通汇总的一致性。

该记录没有触发补读，不能作为补读效果演示。它保留一个未核实字段，两个模型候选意见；不包含 92 条真实盲审样本的候选判断或人工标签。

```powershell
backend/.venv-ci-analysis/Scripts/python.exe scripts/export_field_demo.py --state-dir path/to/synthetic-smoke/state
```

普通使用者无需原数据库，仓库已经包含完整的静态回放。

## 重新录制 README

在独立的工具环境安装 Playwright，并准备 ffmpeg；这些仅是素材制作依赖，不是应用或 CI 核心依赖。

```bash
python -m pip install playwright
python -m playwright install chromium
# 另一个终端：cd frontend && npm ci && npm run dev
python scripts/capture_showcase.py --url http://127.0.0.1:5183
```

已有 Edge/Chromium 时可通过 `--browser-path` 指定可执行文件。输出是浅色、深色 PNG 和 GIF；临时帧与检查记录放在忽略目录 `test-results/showcase`。脚本操作实际按钮、检查 4 个页面、窄屏横向溢出、控制台错误及研究 API 请求数。

GIF 包含四张实际交互画面，各停留 4 秒；它是经过节奏压缩的界面节选，不是模型实时执行录像。使用固定画幅，避免编码器因面板高度变化丢帧。不会插入虚构成功状态或人工标签。

本轮完整离线回归为 230 passed，前端类型检查与构建通过。浏览器已检查字段回放的四个视角、引用导航、深浅主题和 390×844 窄屏，也检查了历史演示的新入口与后续实验链接。GIF 编码后验证为 16 秒、80 帧；机器记录见 [validation.json](results/showcase-v1/validation.json)。
