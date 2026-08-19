"""把模型指标翻译成不带术语的大白话，规则确定性生成，不消耗 LLM 调用。"""

from app.schemas.response import ExecutionMetric, MetricExplanation


def _format_value(value: float) -> str:
    if abs(value) >= 1000 or (0 < abs(value) < 0.001):
        return f"{value:.3g}"
    return f"{value:.4g}"


def _baseline_sentence(metric: ExecutionMetric) -> str:
    if metric.baseline_value is None:
        return ""
    baseline_text = _format_value(metric.baseline_value)
    improvement = metric.relative_improvement
    if improvement is None:
        return f"作为对照，不用模型的最简单猜法能做到 {baseline_text}。"
    if improvement > 0:
        return (
            f"不用模型、按最简单的方法猜是 {baseline_text}，"
            f"模型比它好了约 {improvement:.0%}。"
        )
    return (
        f"注意：不用模型的简单猜法是 {baseline_text}，"
        f"模型反而比它差了约 {abs(improvement):.0%}，说明这个模型没带来提升。"
    )


def _verdict_by_improvement(metric: ExecutionMetric) -> str:
    improvement = metric.relative_improvement
    if improvement is None:
        return "info"
    if improvement >= 0.05:
        return "good"
    if improvement >= 0:
        return "ok"
    return "poor"


def _explain_one(metric: ExecutionMetric) -> MetricExplanation:
    name = metric.name.strip().casefold()
    value = metric.model_value
    value_text = _format_value(value)
    baseline = _baseline_sentence(metric)

    if name in {"r2", "r²", "r_squared"}:
        percent = max(0.0, min(value, 1.0))
        meaning = (
            f"模型能解释数据里约 {percent:.0%} 的变化规律，满分是 1。"
            "一般 0.7 以上算不错，0.5~0.7 算一般，低于 0.5 说明模型只抓住了一半以下的规律。"
        ) + baseline
        verdict = "good" if value >= 0.7 else ("ok" if value >= 0.5 else "poor")
        return MetricExplanation(
            name=metric.name,
            friendly_name="拟合优度 R²",
            value_text=value_text,
            meaning=meaning,
            verdict=verdict,  # type: ignore[arg-type]
        )
    if name in {"rmse", "mae"}:
        friendly = "平均预测偏差" + ("（RMSE）" if name == "rmse" else "（MAE）")
        meaning = (
            f"模型的预测平均偏离真实值约 {value_text}"
            "（单位和预测目标相同），数字越小越准。"
        ) + baseline
        return MetricExplanation(
            name=metric.name,
            friendly_name=friendly,
            value_text=value_text,
            meaning=meaning,
            verdict=_verdict_by_improvement(metric),  # type: ignore[arg-type]
        )
    if name == "mape":
        meaning = (
            f"平均来看，每个预测和真实值差 {value:.1%} 左右，百分比越小越准。"
        ) + baseline
        return MetricExplanation(
            name=metric.name,
            friendly_name="平均百分比误差（MAPE）",
            value_text=f"{value:.1%}",
            meaning=meaning,
            verdict=_verdict_by_improvement(metric),  # type: ignore[arg-type]
        )
    if name in {"accuracy", "balanced_accuracy"}:
        friendly = "预测正确率" + (
            "（各类别平均）" if name == "balanced_accuracy" else ""
        )
        meaning = (
            f"每 100 个样本大约能判断对 {value * 100:.0f} 个。"
        ) + baseline
        verdict = "good" if value >= 0.9 else ("ok" if value >= 0.75 else "poor")
        return MetricExplanation(
            name=metric.name,
            friendly_name=friendly,
            value_text=f"{value:.1%}",
            meaning=meaning,
            verdict=verdict,  # type: ignore[arg-type]
        )
    if name in {"f1", "f1_macro", "f1_score"}:
        meaning = (
            "综合考虑“找得全”和“找得准”的得分，满分 1。"
            "0.8 以上算不错，0.6 以下说明漏判或误判偏多。"
        ) + baseline
        verdict = "good" if value >= 0.8 else ("ok" if value >= 0.6 else "poor")
        return MetricExplanation(
            name=metric.name,
            friendly_name="F1 综合得分",
            value_text=value_text,
            meaning=meaning,
            verdict=verdict,  # type: ignore[arg-type]
        )
    if name == "rank_stability":
        meaning = (
            f"把数据轻微扰动后再排一次名，约 {value:.0%} 的名次保持不变，"
            "越接近 1 说明排名结论越可靠。"
        )
        verdict = "good" if value >= 0.85 else ("ok" if value >= 0.7 else "poor")
        return MetricExplanation(
            name=metric.name,
            friendly_name="排名稳定性",
            value_text=value_text,
            meaning=meaning,
            verdict=verdict,  # type: ignore[arg-type]
        )
    if name == "alternative_method_agreement":
        meaning = (
            f"换一种方法重新算，结论有约 {value:.0%} 是一致的，"
            "越接近 1 说明结论不依赖某一种特定算法。"
        )
        verdict = "good" if value >= 0.85 else ("ok" if value >= 0.7 else "poor")
        return MetricExplanation(
            name=metric.name,
            friendly_name="换方法一致性",
            value_text=value_text,
            meaning=meaning,
            verdict=verdict,  # type: ignore[arg-type]
        )

    # 未识别指标：给通用描述，方向信息尽量利用
    direction = ""
    if metric.higher_is_better is True:
        direction = "这个指标越大越好。"
    elif metric.higher_is_better is False:
        direction = "这个指标越小越好。"
    return MetricExplanation(
        name=metric.name,
        friendly_name=metric.name.upper(),
        value_text=value_text,
        meaning=(direction + baseline) or "模型运行产出的参考指标。",
        verdict=_verdict_by_improvement(metric),  # type: ignore[arg-type]
    )


def explain_metrics(metrics: list[ExecutionMetric]) -> list[MetricExplanation]:
    """为每项指标生成一条小白话解释。"""
    return [_explain_one(metric) for metric in metrics]
