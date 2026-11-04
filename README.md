# Scientific Evidence Agent · 科研证据助手

在固定论文库内，比较多轮 Grep、BM25＋向量检索及证据台账，生成带原文定位的回答与科研报告。

包含 FastAPI 后端、React 文献工作台、数据准备和自动评测命令。主要实验模型为百炼 `qwen3.5-27b`，通过 API 推理；无需本地训练或部署 27B 模型。

## 能力与边界

- 工作区、对话、文本/PDF/PPTX 导入，SQLite 持久化。
- ripgrep 定位、按行阅读、证据 ID、原文引用、工具轨迹。
- 不依赖向量数据库的 BM25＋dense RRF，embedding 使用 `text-embedding-v4`。
- 有 token/时间/步数预算的研究运行和结构化报告。
- QASPER 问答、ArxivDIGESTables 固定字段比较的冻结、隔离运行与离线评分。
- 可选统计分析、文献翻译和主题切换。

引用 ID 有效仅证明可追溯。自动评分不能代替事实与引用支持的人工核验，也不是完整开放网络 Deep Research 排行榜成绩。

## 安装与启动

需要 Python 3.10+、Node.js 20.19+ 或 22.12+，以及 [ripgrep](https://github.com/BurntSushi/ripgrep)。正则检索需要 `rg` 在 PATH 中。

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-research.txt
$env:PYTHONPATH = 'app'
python -m research_backend.cli init
```

`init` 创建 `.env.research` 并生成随机 JWT 密钥，不覆盖已有文件。随后在该文件填写 `RESEARCH_LLM_API_KEY`；启用向量索引时将 `RESEARCH_RAG_ENABLED` 改为 `true`。默认配置：

```dotenv
RESEARCH_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
RESEARCH_LLM_MODEL=qwen3.5-27b
RESEARCH_LLM_EXTRA_BODY={"enable_thinking":false}
RESEARCH_RAG_BACKEND=local
RESEARCH_EMBEDDING_MODEL=text-embedding-v4
```

```powershell
python -m uvicorn research_main:app --app-dir app --env-file .env.research --host 127.0.0.1 --port 8001
```

另一个终端：

```powershell
cd frontend
npm ci
npm run dev
```

打开 `http://127.0.0.1:5183`。开发网关在 Node 进程内签发本地访问令牌；密钥不进入前端。当前方式面向本机使用，不是公网多用户部署方案。

Linux/macOS 使用 `source .venv/bin/activate` 和 `export PYTHONPATH=app`。仅 Grep 和阅读不需要模型 API；模型与 embedding 调用会产生云端费用。

## 评测与验证

协议、过滤规则、命令和指标限制见 [BENCHMARKS.md](docs/BENCHMARKS.md)。冻结规模：QASPER 开发 50 题/测试 200 题；ArxivDIGESTables 改编任务开发 10 个/测试 40 个。开发与测试论文隔离。

已执行的测试、开发集结果和已知问题见 [2026-11-04 验证记录](docs/VALIDATION_2026-11-04.md)。ArxivDIGESTables 当前为待人工核验候选任务，尚不适合用于质量结论。

```powershell
cd backend
python -m pytest tests -q
cd ../frontend
npm run build
```

测试使用假模型，不调用付费 API。数据实验需显式运行。`grep-ledger` 是评测框架的字段证据保留原型，是否优于普通 Grep 仍需实验。

统计分析/XLSX 可安装 `backend/requirements-analysis.txt`；LSTM 另需 `requirements-analysis-lstm.txt`。这些功能不属于 Grep 基准。

## 目录

```text
backend/app/research_backend/             科研 API、检索、报告
backend/app/research_backend/benchmarks/  数据下载、冻结、运行、评分
backend/tests/                           离线回归测试
frontend/src/research/                   科研工作台
docs/BENCHMARKS.md                       实验协议
EXPORT_MANIFEST.json                     精简发布文件校验值
```

发行版保留科研功能，省略旧金融业务、Text2SQL、Milvus 适配、历史 SFT 实验和未使用的前端页面。凭据、数据库、日志、权重、下载数据和原始运行轨迹不上传 GitHub；原始工作目录保留历史记录。
