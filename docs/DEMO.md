# 五分钟证据链演示

## 一条命令启动

安装并启动 Docker Desktop（或 Docker Engine），需要 Docker Compose 2.24+。在仓库根目录运行：

```powershell
docker compose up --build -d --wait
```

打开[论文证据回放](http://127.0.0.1:5183/demo)或[字段状态回放](http://127.0.0.1:5183/demo/fields)。无需在本机安装 Python、Node、ripgrep，也无需模型密钥即可查看回放、导入示例论文和使用 Grep。首次构建需要网络下载公开镜像和依赖；镜像构建完成后，静态回放无需访问模型服务。

工作台位于[本地入口](http://127.0.0.1:5183/)。如 5183 已被占用，PowerShell 先设置 `$env:RESEARCH_DEMO_PORT='5185'`，再执行启动命令，并使用对应端口。Linux/macOS 对应 `RESEARCH_DEMO_PORT=5185 docker compose up --build -d --wait`。

本启动方式用于本机演示，前端仅发布在 127.0.0.1，后端不发布宿主机端口。前端使用已有本地 JWT 网关和 Vite preview；它不代表已交付公网认证、生产部署或多用户服务。

镜像采用科研主线最小依赖。统计分析和 LSTM 的可选依赖组合见 [复现说明](REPRODUCIBILITY.md)。

## 按此顺序演示

| 时间 | 操作 | 要说明的事实 |
|---|---|---|
| 0:00–0:45 | 打开“结论与引用”，查看当时的问题 | 使用真实历史报告；历史模型为 qwen-plus，日期与用量在页面显示 |
| 0:45–1:45 | 分别点两条结论的“查看原文” | evidence ID 对应原运行实际回读；右侧显示局部原文，可展开完整 1–80 行窗口、来源版本和全文 |
| 1:45–2:30 | 切换“工具轨迹” | 本例先回读两篇摘要，再检索规模关键词；展示原始调用顺序与参数，不编造更理想的搜索轨迹 |
| 2:30–3:30 | 切换“失败定位”，对照两个窗口 | 原始报告将“所读片段未找到”扩展成“论文没有报告”；事后定位的 453–470 行不是原运行证据 |
| 3:30–4:15 | 查看“核验补读 · 实验” | 历史 6 题先导：1 题改善、5 题不变；同时查看后续 20 题五组实验，未确认定向补读优于普通补搜。不同于当前字段控制器 |
| 4:15–5:00 | 点“用示例论文开始研究” | 创建独立课题、导入两篇开放论文，进入真实工作台；新运行拥有新的来源与证据 ID |

新课题可先用资料页的检索与阅读验证导入。完整问题如下，可复制到工作台；实际生成需要完成下面的模型配置。

> 只使用工作区内两篇论文，搜索并阅读原文后比较 QASPER 和 ArxivDIGESTables 的研究任务与数据规模（问题数/表格数、涉及论文数）。给出 2–4 条带证据的结论，每篇一个对比行，并说明为什么不能直接比较二者的方法性能。未找到的内容写“本次未核实”，不使用网络。

## 字段状态回放

打开 [http://127.0.0.1:5183/demo/fields](http://127.0.0.1:5183/demo/fields)，依次查看四个视角：

1. **字段台账**：三个预设字段，两项候选、一项未核实；点击原文按钮。
2. **原文证据**：检查实际回读的五行文本、evidence ID 和来源版本。
3. **版本记录**：对比中间候选与最终报告的字段值；修改不自动等于改善。
4. **停止与成本**：5 次调用、15,814 tokens、0 轮补读；因预留收尾预算结束，语义成功仍为 null。

这是合成资料上的真实模型接通记录，没有触发补读，不能用它声称补救有效或语义准确。页面复用生产字段组件，但操作只读取静态文件，不会发起研究、扣模型额度或修改数据库。可切换浅色/深色并下载完整回放 JSON。素材来源与录制方法见 [SHOWCASE.md](SHOWCASE.md)。

## 配置真实模型运行

将根目录 `.env.demo.example` 复制为 `.env.demo`，在本机编辑 `RESEARCH_LLM_API_KEY`，保留示例中的模型和服务地址或按已有服务调整。

```powershell
if (!(Test-Path .env.demo)) { Copy-Item .env.demo.example .env.demo }
# 使用编辑器填写本地 .env.demo。
docker compose up -d --force-recreate backend
```

`.env.demo` 由 Git 忽略，不会进入镜像构建上下文，也不会传给前端容器。模型调用使用你的云服务额度。配置完成后再从工作台提交新任务；回放页始终显示固定历史记录。

## 数据保存与停止

| 位置 | 内容 |
|---|---|
| 命名卷 `scientific-evidence-agent_research-data` | SQLite、资料正文、证据和工作区数据 |
| 命名卷 `scientific-evidence-agent_demo-config` | 首次启动时随机生成的本地 JWT 配置；后续启动保留 |
| `.env.demo` | 可选的本机云模型配置，只提供给后端 |

`docker compose stop` 停止运行；`docker compose up -d --wait` 再次启动。`docker compose down` 移除本项目容器与网络并保留卷。保留卷即可保留课题与认证配置；不需要重建数据库。

## 可重复验证

```powershell
python scripts/smoke_demo.py
docker compose run --rm config
docker compose restart backend frontend
docker compose up -d --wait
python scripts/smoke_demo.py --verify-state
```

检查脚本使用 Python 标准库，创建一个演示检查课题，验证两个来源版本、原文逐字一致、JWT 代理、真实 rg 检索、Origin 限制及重启持久化，不调用模型。GitHub Actions 的 Docker demo 作业执行同样的流程。

## 记录与来源

回放入口通过静态资源即可运行，浏览器读取 `frontend/public/demo/recording.json`，不依赖原开发者数据库。该 JSON 包含原始问题、完整报告、工具地址与两个原始 evidence ID；保留错误声明供核对。论文 TXT 与块信息可从页面下载，署名、版本与许可见 [ATTRIBUTION.md](../frontend/public/demo/ATTRIBUTION.md)。两篇论文的原始 arXiv 页面均链接到 CC BY 4.0。

`scripts/export_demo.py` 提供从指定只读本地数据库重新导出同一历史记录的流程。普通使用者无需拥有原数据库，仓库已包含经完整性检查的导出文件。`backend/tests/test_demo_recording.py` 验证正文重新导入后版本与行号不变、引用来自真实回读，以及事后核对窗口没有混入原始证据。

初版只完成 Docker 接口检查。2026-11-07 已补做字段回放的四视图、引用跳转、浅色/深色和窄屏浏览器检查，并录制 README 动图；录制器验证没有研究 API 调用。Docker CI 同时检查两个回放入口与字段记录。
