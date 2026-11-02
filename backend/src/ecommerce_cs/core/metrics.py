"""轻量指标：进程内计数器，输出 Prometheus 文本格式。"""

from __future__ import annotations

from collections import Counter

_counters: Counter[str] = Counter()


def incr(name: str, value: int = 1) -> None:
    if value:
        _counters[name] += value


def snapshot() -> dict[str, int]:
    return dict(_counters)


def render_prometheus() -> str:
    lines = [
        "# HELP cs_counter_total 业务计数器",
        "# TYPE cs_counter_total counter",
    ]
    for name, value in sorted(_counters.items()):
        metric = name if name.endswith("_total") else f"{name}_total"
        lines.append(f"cs_{metric} {value}")
    return "\n".join(lines) + "\n"
