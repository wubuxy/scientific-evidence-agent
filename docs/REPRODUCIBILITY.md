# 安装与离线回归

科研主线不需要 scipy、pandas、openpyxl 或 torch。模型和 embedding 的实际调用需要单独配置云端服务；以下回归使用替代模型，不需要 API key，也不会调用付费模型。

## 环境与依赖

- Python 3.10+；CI 配置选择 3.10 和 3.13。
- Node.js 20.19+ 或 22.12+；CI 配置选择 Node 24。
- ripgrep（`rg`）必须加入 PATH，供 Agent 正则搜索和定向补读使用。缺少它时，字面搜索仍可使用，正则请求会明确返回不可用。

安装 ripgrep：Windows 使用 `winget install BurntSushi.ripgrep.MSVC` 或 `choco install ripgrep`，Ubuntu/Debian 使用 `sudo apt-get install ripgrep`，macOS 使用 `brew install ripgrep`。打开新终端后执行 `rg --version`；仅安装 VS Code 并不保证普通终端能够找到 `rg`。

先创建独立环境，避免本机已有的可选包改变测试口径。在 `backend` 下执行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-research.txt -c constraints-ci.txt
python -m pip check
python -m pytest tests -q --junitxml=test-results/core.xml
```

Linux/macOS 使用 `source .venv/bin/activate` 激活环境。

| 依赖组合 | 安装方式（在核心依赖之上） | 覆盖范围 |
|---|---|---|
| 核心 | `requirements-research.txt` | 科研 API、检索、原文回读、引用约束、预算、评测协议、CSV、翻译和任务取消 |
| 分析 | `python -m pip install -r requirements-analysis.txt -c constraints-ci.txt` | 追加统计分析和 XLSX；LSTM 测试仍需 torch |
| 完整 | 上述分析依赖加 `requirements-analysis-lstm.txt` 中的 torch | 追加 LSTM 数据隔离与训练检查 |

CPU 环境可按以下方式补齐完整依赖：

```powershell
python -m pip install -r requirements-analysis.txt -c constraints-ci.txt
python -m pip install "torch>=2.2,<3" --index-url https://download.pytorch.org/whl/cpu
python -m pip check
python -m pytest tests -q --junitxml=test-results/full.xml
```

`constraints-ci.txt` 固定核心与分析依赖及其传递依赖，包含 Python 版本和平台标记；通过 `-c` 使用它不会额外安装分析包。torch 仍按现有可选依赖范围安装，其实际版本应随结果记录。前端使用已提交的 `package-lock.json` 和 `npm ci`。

## 缺少依赖时的行为

```powershell
python -m pytest tests -q --without-rg --junitxml=test-results/without-rg.xml
```

`--without-rg` 仅在当前测试进程中模拟无法发现 `rg`，不修改系统 PATH，也不卸载程序。只有标记为 `requires_rg` 的三个真实集成测试会跳过；字面降级、正则不可用错误，以及不依赖外部搜索的分支成本核算仍运行。

scipy 和 openpyxl 的检查发生在对应测试中。缺少 scipy 不会跳过 CSV、翻译、来源对齐、工作区隔离和任务取消测试；缺少 torch 仅影响 LSTM 测试。`pytest -q` 显示通过、跳过及原因，不把“收集数”写成“通过数”。

## 本地验证记录

截至 2026-11-05，本机 Windows 验证如下，所有组合均收集 140 项：

| 环境 | 通过 | 跳过 | 失败 |
|---|---:|---:|---:|
| Python 3.10，干净核心依赖环境，含 rg | 127 | 13（统计、XLSX、LSTM） | 0 |
| 同一核心环境，模拟缺少 rg | 124 | 16（上述 13 项加 3 项 rg 集成） | 0 |
| Python 3.13，干净分析依赖环境，含 rg，无 torch | 139 | 1（LSTM） | 0 |
| Python 3.13，同一干净环境补齐 CPU torch 2.15.0 | 140 | 0 | 0 |
| Python 3.10，已有完整依赖环境，含 rg 和 CPU torch | 140 | 0 | 0 |

干净环境的核心和分析依赖按提交的约束文件安装，CPU torch 从官方索引补齐；已有完整环境的 140 项结果单独列出。机器可读结果及完整环境的主要包版本见 [validation-summary.json](results/validation-summary.json) 的 `checks`。模型质量实验仍沿用原有结果，本轮工程回归不增加独立评测题数。

## 持续集成

[CI 工作流](../.github/workflows/ci.yml) 在 main 分支推送、PR 和手动触发时运行：

| 作业 | 配置 |
|---|---|
| 核心后端 | Ubuntu / Python 3.10；Windows / Python 3.13；仅安装核心依赖 |
| 完整后端 | Ubuntu / Python 3.13；安装分析依赖和 CPU PyTorch |
| 缺少 rg | Ubuntu 核心环境再次执行 `--without-rg` |
| 前端 | Node 24；`npm ci`、类型检查、生产构建 |

后端作业先安装并检查真实 `rg`，再运行回归。每次保存 JUnit XML 和已安装版本，作为保留 14 天的 Actions artifact。配置完成与 GitHub 托管运行通过分别记录；本地结果不能替代尚未执行的远端 CI 状态。

更新依赖约束时，在 `backend` 执行以下命令并重新验证环境矩阵：

```powershell
uv pip compile requirements-research.txt requirements-analysis.txt --universal --python-version 3.10 --output-file constraints-ci.txt --no-annotate
```
