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

`--without-rg` 仅在当前测试进程中模拟无法发现 `rg`，不修改系统 PATH，也不卸载程序。只有标记为 `requires_rg` 的五个真实集成测试会跳过；字面降级、正则不可用错误，以及不依赖外部搜索的分支成本核算仍运行。

scipy 和 openpyxl 的检查发生在对应测试中。缺少 scipy 不会跳过 CSV、翻译、来源对齐、工作区隔离和任务取消测试；缺少 torch 仅影响 LSTM 测试。`pytest -q` 显示通过、跳过及原因，不把“收集数”写成“通过数”。

## 本地验证记录

截至 2026-11-06，加入核验失败回退、缓存策略回放的路由与资源检查后，Windows 完整环境收集并通过 195 项。原人工阅读包阶段为 187 项。下表同时保留此前的 Ubuntu 核心环境记录，收集数分开列出：

| 环境 | 收集 | 通过 | 跳过 | 失败 |
|---|---:|---:|---:|---:|
| Windows / Python 3.13，完整环境，含 rg 和 CPU torch 2.15.0，本轮 | 195 | 195 | 0 | 0 |
| A5000 主机 Ubuntu / Python 3.12，核心依赖环境，含 rg，上一提交 | 186 | 173 | 13（统计、XLSX、LSTM） | 0 |

干净环境的核心和分析依赖按提交的约束文件安装，CPU torch 从官方索引补齐。140、142、152、163、168、177、186 项的历史矩阵分别保存在机器记录的 `offline_test_profiles_before_demo`、`offline_test_profiles_before_agent_evals`、`offline_test_profiles_before_eval_completion`、`offline_test_profiles_before_verifier_pilot`、`offline_test_profiles_before_verifier_scale`、`offline_test_profiles_before_verifier_labels`、`offline_test_profiles_before_human_review` 中；最新结果和包版本见 [validation-summary.json](results/validation-summary.json) 的 `checks`。历史 163 项时 Windows 核心环境为 150 passed / 13 skipped，缺 rg 为 145 passed / 18 skipped；这些历史数值不冒充最新运行。

端到端 Agent CLI 使用本地假模型端点，覆盖两轮五组分支、两次生产报告、六个声明／单元格和显式合成标注校准，不产生云模型费用。新增的真实单卡核验器训练与 Agent 效果分开统计，完整环境、适配器、评分与局限见 [A5000 pilot 结果](results/SCIFACT_VERIFIER_PILOT_V1.md)。在仓库根目录执行 `python scripts/audit_verifier_pilot.py` 可复查已发布的原始预测和测试分数，无需下载模型。

第二轮增加了 1.5B/7B 双精度、三种子 7B BF16 LoRA 与 MiniCheck 外部基线；结果与显存口径见[规模对照](results/SCIFACT_VERIFIER_SCALE_V2.md)。`python scripts/audit_verifier_scale.py --bootstrap` 无需权重即可独立复算固定分母、二分类/三分类点估计及三项校正区间；中断版本也可单独审计。GPU 训练环境使用独立的 `requirements-verifier-gpu.txt`，不混入应用或 CI 的核心依赖。

第三轮完成六次 7B BF16 LoRA 标签干预对照与 1,446 条新预测。运行 `python scripts/audit_verifier_labels.py --bootstrap` 可无 GPU 重算三种标签口径、校准阈值和主要配对区间。数据与代码在运行前冻结，模型和语料保存在服务器 `D:\Ding`，仓库保留预测与评分元数据，见[标签干预报告](results/SCIFACT_LABEL_ABLATION_V1.md)。

## 持续集成

[CI 工作流](../.github/workflows/ci.yml) 在 main 分支推送、PR 和手动触发时运行：

| 作业 | 配置 |
|---|---|
| 核心后端 | Ubuntu / Python 3.10；Windows / Python 3.13；仅安装核心依赖 |
| 完整后端 | Ubuntu / Python 3.13；安装分析依赖和 CPU PyTorch |
| 缺少 rg | Ubuntu 核心环境再次执行 `--without-rg` |
| 前端 | Node 24；`npm ci`、类型检查、生产构建 |
| Docker demo | 构建与健康启动、无模型的论文导入 / 检索检查、配置初始化幂等性、重启持久化 |

后端作业先安装并检查真实 `rg`，再运行回归、已发布 pilot、规模对照的完整/中断版本与标签干预审计；完整环境还独立复算主要置信区间。每次保存 JUnit XML 和已安装版本，作为保留 14 天的 Actions artifact。配置完成与 GitHub 托管运行通过分别记录；本地结果不能替代尚未执行的远端 CI 状态。

提交 `4c36c8f` 的三项后端、前端与 Docker demo 共五个 GitHub 作业已通过：[运行记录](https://github.com/wubuxy/scientific-evidence-agent/actions/runs/42060005247)。后续提交的状态以对应 Actions 运行记录为准。Docker 启动与回放边界见 [演示说明](DEMO.md)。

更新依赖约束时，在 `backend` 执行以下命令并重新验证环境矩阵：

```powershell
uv pip compile requirements-research.txt requirements-analysis.txt --universal --python-version 3.10 --output-file constraints-ci.txt --no-annotate
```
