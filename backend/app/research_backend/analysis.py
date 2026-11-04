"""Fixed, inspectable numerical methods. User content is never executed as code."""
import math
import platform
import hashlib
from pathlib import Path
from importlib.metadata import version

import numpy as np
from scipy import stats

METHOD_NAMES = {"descriptive": "描述统计", "welch_t": "Welch 独立样本 t 检验", "paired_t": "配对 t 检验", "chi_square": "卡方独立性检验", "correlation": "相关性分析", "linear_regression": "一元线性回归", "hotelling_t2": "单样本 Hotelling T² 检验", "lstm": "LSTM 单变量时间序列预测"}


def finite(value):
    value = float(value)
    return value if math.isfinite(value) else None


def metrics(actual, predicted):
    residual = np.asarray(actual) - np.asarray(predicted)
    return dict(mae=finite(np.mean(np.abs(residual))), rmse=finite(np.sqrt(np.mean(residual ** 2))))


def summarize(values):
    if len(values) == 0:
        return dict(n=0, mean=None, std=None, median=None, min=None, max=None, q1=None, q3=None)
    return dict(n=len(values), mean=finite(np.mean(values)), std=finite(np.std(values, ddof=1)) if len(values) > 1 else None,
                median=finite(np.median(values)), min=finite(np.min(values)), max=finite(np.max(values)),
                q1=finite(np.quantile(values, .25)), q3=finite(np.quantile(values, .75)))


def complete_matrix(data, columns):
    indexes = [data["columns"].index(name) for name in columns]
    rows = [[row[index] for index in indexes] for row in data["rows"]]
    matrix = np.asarray([[float(value) if value is not None else np.nan for value in row] for row in rows], dtype=float)
    if np.any(np.isinf(matrix)):
        raise ValueError("分析列含有非有限数值")
    return matrix


def compute_analysis(data, params, progress=lambda value: None, artifact_dir=None):
    method, columns = params["method"], params["columns"]
    if any(name not in data["columns"] for name in columns):
        raise ValueError("所选分析列不存在")
    warnings = []
    result = dict(method=method, title=METHOD_NAMES[method], parameters=params, software={"python": platform.python_version(), "numpy": version("numpy"), "scipy": version("scipy"),
                  "implementation": "research-lab-v1", "implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
                  warnings=warnings, tables=[], chart=None, interpretation="", assumptions=[])
    if method == "chi_square":
        a, b = [data["columns"].index(name) for name in columns]
        weight = params.get("weight_column")
        if weight and (weight not in data["columns"] or weight in columns):
            raise ValueError("频数列必须存在，且不能与分类变量相同")
        wi = data["columns"].index(weight) if weight else None
        rows = [row for row in data["rows"] if row[a] is not None and row[b] is not None and (wi is None or row[wi] is not None)]
        levels_a, levels_b = sorted({str(row[a]) for row in rows}), sorted({str(row[b]) for row in rows})
        if not (2 <= len(levels_a) <= 30 and 2 <= len(levels_b) <= 30):
            raise ValueError("卡方检验的两个分类变量分别需要 2–30 个类别")
        counts = np.zeros((len(levels_a), len(levels_b)))
        for row in rows:
            count = float(row[wi]) if wi is not None else 1
            if not math.isfinite(count) or count < 0 or count != int(count) or count > 1e9:
                raise ValueError("频数必须为非负整数，单行不超过 10 亿")
            counts[levels_a.index(str(row[a])), levels_b.index(str(row[b]))] += count
        if np.any(counts.sum(axis=0) == 0) or np.any(counts.sum(axis=1) == 0):
            raise ValueError("列联表不能包含总频数为零的行或列")
        test = stats.chi2_contingency(counts, correction=params["continuity_correction"])
        if np.any(test.expected_freq < 5):
            warnings.append("存在期望频数小于 5 的单元格，卡方近似可能不可靠；2×2 表可结合 Fisher 精确检验。")
        result.update(statistic=finite(test.statistic), p_value=finite(test.pvalue), degrees_of_freedom=int(test.dof),
                      effect_size=dict(name="Cramér's V（基于当前校正后的统计量）", value=finite(np.sqrt(test.statistic / (counts.sum() * min(counts.shape[0] - 1, counts.shape[1] - 1))))),
                      sample_size=int(counts.sum()), excluded_rows=len(data["rows"]) - len(rows))
        result["tables"] = [dict(title="观测频数", columns=[columns[0] + " / " + columns[1]] + levels_b, rows=[[label] + counts[i].tolist() for i, label in enumerate(levels_a)]),
                            dict(title="期望频数", columns=[columns[0] + " / " + columns[1]] + levels_b, rows=[[label] + test.expected_freq[i].tolist() for i, label in enumerate(levels_a)])]
        if counts.shape == (2, 2):
            fisher = stats.fisher_exact(counts.astype(np.int64))
            result["fisher_exact"] = dict(odds_ratio=finite(fisher.statistic), p_value=finite(fisher.pvalue))
        result["assumptions"] = ["观测独立；输入为类别频数而非均值或百分比。", "同一受试对象的重复观测不应当作独立样本。"]
    elif method == "lstm":
        return lstm_analysis(data, params, result, progress, artifact_dir)
    else:
        try:
            matrix = complete_matrix(data, columns)
        except (TypeError, ValueError) as exc:
            raise ValueError("该方法需要数值列，请检查非数值单元格") from exc
        complete = matrix[np.all(np.isfinite(matrix), axis=1)]
        separate = [matrix[np.isfinite(matrix[:, i]), i] for i in range(len(columns))]
        result["tables"].append(dict(title="样本概况", columns=["变量", "有效 n", "缺失 n", "均值", "标准差", "中位数", "最小值", "最大值"],
            rows=[[name, len(values), len(matrix) - len(values), *[summarize(values)[key] for key in ("mean", "std", "median", "min", "max")]] for name, values in zip(columns, separate)]))
        if method == "descriptive":
            result["interpretation"] = "逐列统计非缺失数值；未填补缺失值，也未进行显著性检验。"
            result["summaries"] = {name: summarize(values) for name, values in zip(columns, separate)}
            return result
        if method in {"paired_t", "correlation", "linear_regression", "hotelling_t2"}:
            result["sample_size"] = len(complete)
            result["excluded_rows"] = len(matrix) - len(complete)
            warnings.append("仅使用所选各列同时非缺失的完整行；没有分别删除后重新配对。")
        if method in {"welch_t", "paired_t"}:
            x, y = (complete[:, 0], complete[:, 1]) if method == "paired_t" else separate
            if min(len(x), len(y)) < 2:
                raise ValueError("每组至少需要两个有效观测值")
            if method == "paired_t" and np.std(x - y, ddof=1) <= 0:
                raise ValueError("配对差值没有变异，无法进行有效的 t 检验")
            if method == "welch_t" and np.var(x, ddof=1) + np.var(y, ddof=1) <= 0:
                raise ValueError("两组都没有变异，无法进行有效的 t 检验")
            test = stats.ttest_rel(x, y) if method == "paired_t" else stats.ttest_ind(x, y, equal_var=False)
            ci = test.confidence_interval(confidence_level=1 - params["alpha"])
            effect = (np.mean(x - y) / np.std(x - y, ddof=1)) if method == "paired_t" else ((np.mean(x) - np.mean(y)) / np.sqrt(((len(x) - 1) * np.var(x, ddof=1) + (len(y) - 1) * np.var(y, ddof=1)) / (len(x) + len(y) - 2)))
            result.update(statistic=finite(test.statistic), p_value=finite(test.pvalue), degrees_of_freedom=finite(test.df),
                          mean_difference=finite(np.mean(x) - np.mean(y)), confidence_interval=dict(level=1 - params["alpha"], low=finite(ci.low), high=finite(ci.high)),
                          effect_size=dict(name="Cohen's dz" if method == "paired_t" else "Cohen's d（合并组内标准差）", value=finite(effect)), sample_sizes=[len(x), len(y)])
            if method == "welch_t":
                result["sample_size"] = len(x) + len(y)
            result["assumptions"] = ["配对行须为同一对象 / 数据划分上的对应观测；差值近似正态。" if method == "paired_t" else "两组观测独立；Welch 检验不要求方差相等。", "小样本时应检查异常值与分布；p 值不代表效应大小或因果关系。"]
        elif method == "correlation":
            if len(complete) < 3 or any(np.std(complete[:, i]) <= 0 for i in range(2)):
                raise ValueError("相关分析至少需要三个完整观测，且两列都不能为常数")
            pearson = stats.pearsonr(complete[:, 0], complete[:, 1])
            spearman = stats.spearmanr(complete[:, 0], complete[:, 1])
            result.update(pearson=dict(coefficient=finite(pearson.statistic), p_value=finite(pearson.pvalue)),
                          spearman=dict(coefficient=finite(spearman.statistic), p_value=finite(spearman.pvalue)),
                          interpretation="同时报告 Pearson 线性相关与 Spearman 秩相关；没有对两项检验作多重比较校正。相关关系不等于因果关系。")
            result["chart"] = dict(type="scatter", x_label=columns[0], y_label=columns[1], points=complete[::max(1, len(complete) // 500)][:500].tolist())
            result["assumptions"] = ["行间观测独立；Pearson 的检验前提应结合联合分布和异常值检查。"]
        elif method == "linear_regression":
            if len(complete) < 3 or any(np.std(complete[:, i]) <= 0 for i in range(2)):
                raise ValueError("回归至少需要三个完整观测，自变量和响应变量都不能为常数")
            fit = stats.linregress(complete[:, 0], complete[:, 1])
            critical = stats.t.ppf(1 - params["alpha"] / 2, len(complete) - 2)
            result.update(slope=finite(fit.slope), intercept=finite(fit.intercept), r_squared=finite(fit.rvalue ** 2),
                          p_value=finite(fit.pvalue), statistic=finite(fit.slope / fit.stderr) if fit.stderr else None,
                          confidence_interval=dict(level=1 - params["alpha"], low=finite(fit.slope - critical * fit.stderr), high=finite(fit.slope + critical * fit.stderr)))
            result["chart"] = dict(type="scatter", x_label=columns[0], y_label=columns[1], points=complete[::max(1, len(complete) // 500)][:500].tolist(), slope=finite(fit.slope), intercept=finite(fit.intercept))
            result["assumptions"] = ["线性关系、独立误差、同方差；置信区间还需残差分布条件。", "R² 为样本内拟合指标，不是预测测试集成绩。"]
        elif method == "hotelling_t2":
            n, p = complete.shape
            if p < 2 or n <= p:
                raise ValueError("Hotelling T² 至少需要两列，且完整样本数必须大于变量数")
            covariance = np.cov(complete, rowvar=False)
            if np.linalg.cond(covariance) > 1e10:
                raise ValueError("协方差矩阵奇异或病态，请去掉常数列或共线变量")
            mean = complete.mean(axis=0)
            t2 = float(n * mean @ np.linalg.solve(covariance, mean))
            f_value = (n - p) * t2 / (p * (n - 1))
            result.update(statistic=finite(t2), f_statistic=finite(f_value), p_value=finite(stats.f.sf(f_value, p, n - p)),
                          null_mean=[0] * p, degrees_of_freedom=[p, n - p])
            result["assumptions"] = ["单样本检验，原假设为所选变量的均值向量全部为 0。", "适用于独立、多元近似正态观测；协方差矩阵须可逆。对其他基准应先将数据减去对应基准值。"]
    if result.get("p_value") is not None:
        result["reject_null"] = result["p_value"] < params["alpha"]
        result["interpretation"] = f"在 α={params['alpha']} 下，{'拒绝' if result['reject_null'] else '未能拒绝'}该检验的原假设。未进行多重比较校正；请结合效应量、置信区间与数据收集方式解释。"
    return result


def lstm_analysis(data, params, result, progress, artifact_dir):
    try:
        import torch
        from torch import nn
    except ImportError as exc:
        raise ValueError("LSTM 需要安装 requirements-analysis-lstm.txt 中的 PyTorch") from exc
    import pandas as pd
    from pathlib import Path
    from .lstm_model import ForecastLSTM
    index = data["columns"].index(params["columns"][0])
    rows = data["rows"]
    time_column = params.get("time_column")
    if time_column:
        if time_column not in data["columns"] or time_column == params["columns"][0]:
            raise ValueError("请选择与预测目标不同的时间列")
        ti = data["columns"].index(time_column)
        raw_time = [row[ti] for row in rows]
        try:
            time_values = np.asarray(raw_time, dtype=float) if all(isinstance(v, (int, float)) for v in raw_time) else pd.to_datetime(raw_time, errors="raise", utc=True).asi8.astype(float)
        except Exception as exc:
            raise ValueError("时间列需要有效的日期或数值，不得包含空值") from exc
        if any(value is None for value in raw_time) or not np.all(np.isfinite(time_values)) or len(np.unique(time_values)) != len(time_values):
            raise ValueError("时间列不得缺失或重复")
        order = np.argsort(time_values, kind="stable")
        sorted_time = time_values[order]
        intervals = np.diff(sorted_time)
        if not np.allclose(intervals, intervals[0], rtol=1e-6, atol=1e-6):
            raise ValueError("当前 LSTM 需要等间隔序列；请先整理不规则时间或明确按行顺序建模")
        rows = [rows[i] for i in order]
    else:
        result["warnings"].append("未指定时间列，按原始数据行顺序视为等间隔序列。")
    if not 48 <= len(rows) <= 5000:
        raise ValueError("首版交互式 LSTM 支持 48–5000 个时间点")
    try:
        values = np.asarray([float(row[index]) if row[index] is not None else np.nan for row in rows], dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError("LSTM 目标列必须为数值") from exc
    if not np.all(np.isfinite(values)):
        raise ValueError("LSTM 不自动删除或填补缺失值，请先整理连续、完整的序列")
    cut, window = int(len(values) * params["train_fraction"]), params["window"]
    if cut <= window + 8 or len(values) - cut < 8:
        raise ValueError("训练序列过短或测试点少于 8，请减小窗口或增加数据")
    mean, scale = float(values[:cut].mean()), float(values[:cut].std())
    if not math.isfinite(scale) or scale <= 1e-12:
        raise ValueError("训练段没有足够变异，不能训练 LSTM")
    standardized = (values - mean) / scale
    if not np.all(np.isfinite(standardized)) or np.max(np.abs(standardized)) > 1e8:
        raise ValueError("序列尺度过于极端，请先检查异常值和单位")
    torch.set_num_threads(1)
    torch.manual_seed(params["seed"])
    torch.use_deterministic_algorithms(True)
    x = np.stack([standardized[i-window:i] for i in range(window, len(values))]).astype(np.float32)
    y = standardized[window:].astype(np.float32)
    train_size = cut - window
    train_x = torch.from_numpy(x[:train_size, :, None])
    train_y = torch.from_numpy(y[:train_size, None])
    model = ForecastLSTM(params["hidden_size"])
    optimizer = torch.optim.Adam(model.parameters(), lr=0.01)
    loss_fn = nn.MSELoss()
    losses = []
    for epoch in range(params["epochs"]):
        model.train()
        total = 0.
        for start in range(0, train_size, 64):
            bx, by = train_x[start:start+64], train_y[start:start+64]
            optimizer.zero_grad()
            loss = loss_fn(model(bx), by)
            if not torch.isfinite(loss):
                raise ValueError("训练出现非有限损失，请检查数据尺度")
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            total += float(loss.detach()) * len(bx)
        losses.append(total / train_size)
        progress(dict(epoch=epoch + 1, epochs=params["epochs"], loss=losses[-1]))
    model.eval()
    with torch.no_grad():
        predicted = model(torch.from_numpy(x[train_size:, :, None])).numpy().ravel().astype(float) * scale + mean
        history, forecast = standardized[-window:].tolist(), []
        for _ in range(params["horizon"]):
            value = float(model(torch.tensor(history[-window:], dtype=torch.float32).reshape(1, window, 1)).item())
            history.append(value)
            forecast.append(value * scale + mean)
    actual, baseline = values[cut:], values[cut-1:-1]
    scores, naive_scores = metrics(actual, predicted), metrics(actual, baseline)
    stride = max(1, math.ceil(len(actual) / 400))
    result.update(software={**result["software"], "torch": version("torch")},
                  sample_size=len(values), train_points=cut, test_points=len(values) - cut,
                  training_windows=train_size, split_index=cut, scaler=dict(mean=mean, std=scale, fitted_on="training_prefix_only"),
                  evaluation="rolling_one_step", metrics=scores, baseline_metrics=naive_scores,
                  baseline="前一个已观测值（naive persistence）", forecast=[finite(v) for v in forecast], loss_history=losses,
                  beats_baseline=scores["rmse"] < naive_scores["rmse"],
                  chart=dict(type="series", x_label="测试集时间点", y_label=params["columns"][0], points=[dict(index=cut+i, actual=finite(actual[i]), predicted=finite(predicted[i]), baseline=finite(baseline[i])) for i in range(0, len(actual), stride)]),
                  interpretation="按时间顺序划分训练 / 测试；标准化仅拟合训练段。测试为滚动单步预测，每步可使用此前真实观测，不是整段自由滚动预测。未来点为递归预测，未提供不确定性区间。",
                  assumptions=["数据是等间隔、连续观测序列；未自动填补缺失值。", "固定训练轮数，无测试集调参；重复尝试后需另留最终测试集。", "LSTM 不保证超过简单基线，预测结果不等于论文实验结论。"])
    if not result["beats_baseline"]:
        result["warnings"].append("本次 LSTM 的测试 RMSE 未优于简单持久性基线，应先检查数据与任务，而不是直接增加模型复杂度。")
    if artifact_dir:
        torch.save(dict(state_dict=model.state_dict(), hidden_size=params["hidden_size"], window=window, mean=mean, scale=scale,
                        seed=params["seed"], architecture="ForecastLSTM-v1", implementation_sha256=result["software"]["implementation_sha256"]), Path(artifact_dir) / "model.pt")
        result["model_artifact"] = "model.pt"
    return result
