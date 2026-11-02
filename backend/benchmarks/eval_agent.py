"""回答级端到端评测：路由、工具调用、事实覆盖与越权声明拦截。

与 RAG 检索评测（eval_retrieval.py）互补，这里直接跑完整 Agent 图，
统计“用户问题到最终回答”的任务级指标。

评测口径：
- 路由准确率：最终 state.intent 是否等于人工标注意图
- 工具匹配率：必须调用的工具是否都调用到 / 候选工具是否命中至少一个
- 事实覆盖率：回答是否包含标注的订单号、状态、价格、政策要点
- 安全拦截：回答中不得出现未确认的成功声明或手机号等敏感信息
- 审批正确性：该进确认弹窗的操作是否真的进入弹窗
- 端到端通过率：以上全部满足且无异常

用法：
    python benchmarks/eval_agent.py
    python benchmarks/eval_agent.py --limit 5
    python benchmarks/eval_agent.py --output ../docs/回答质量评测报告.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR / "src"))

from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402
from langgraph.checkpoint.memory import MemorySaver  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402

from ecommerce_cs.agents.graph import build_graph  # noqa: E402
from ecommerce_cs.core.security import hash_phone  # noqa: E402
from ecommerce_cs.core.settings import get_settings  # noqa: E402
from ecommerce_cs.db.models import Conversation, Message, PendingAction, User  # noqa: E402
from ecommerce_cs.db.repositories import chat as chat_repo  # noqa: E402
from ecommerce_cs.db.session import SessionLocal, dispose_engine  # noqa: E402
from ecommerce_cs.services.chat import _last_ai  # noqa: E402

CASES_FILE = Path(__file__).with_name("golden_qa.jsonl")
DEFAULT_OUTPUT = BACKEND_DIR.parent / "docs" / "回答质量评测报告.md"
DEFAULT_PHONE = "13800001001"


@dataclass
class CaseResult:
    case_id: str
    category: str
    query: str
    answer: str = ""
    intent_expected: str | None = ""
    intent_actual: str = ""
    tools: list[str] = field(default_factory=list)
    pending_actual: bool = False
    pending_expected: bool | None = None
    forbidden_hits: list[str] = field(default_factory=list)
    intent_ok: bool = False
    tools_ok: bool = True
    facts_ok: bool = True
    pending_ok: bool = True
    tools_checked: bool = True
    facts_checked: bool = True
    latency_ms: float = 0.0
    error: str = ""

    @property
    def passed(self) -> bool:
        return (
            not self.error
            and self.intent_ok
            and self.tools_ok
            and self.facts_ok
            and not self.forbidden_hits
            and self.pending_ok
        )

    @property
    def reason(self) -> str:
        if self.error:
            return f"异常：{self.error}"
        problems: list[str] = []
        if not self.intent_ok:
            problems.append(f"意图 {self.intent_actual or '-'} ≠ {self.intent_expected}")
        if not self.tools_ok:
            problems.append(f"工具未命中（实际 {', '.join(self.tools) or '无'}）")
        if not self.facts_ok:
            problems.append("回答缺少事实要点")
        if self.forbidden_hits:
            problems.append(f"出现禁止表述：{', '.join(self.forbidden_hits)}")
        if not self.pending_ok:
            problems.append(f"审批状态不符（实际 {self.pending_actual}）")
        return "；".join(problems) or "通过"


def _load_cases(limit: int | None) -> list[dict]:
    cases = [
        json.loads(line)
        for line in CASES_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return cases[:limit] if limit else cases


def _normalize(text: str) -> str:
    """去掉千分位逗号与空白，"5,999" 与 "5999" 视为同一事实。"""
    return re.sub(r"[\s,，]", "", text).lower()


def _contains(answer: str, keyword: str) -> bool:
    return _normalize(keyword) in _normalize(answer)


def _check_tools(case: dict, tools: list[str]) -> bool:
    must = case.get("must_tools") or []
    any_of = case.get("any_tools") or []
    if must and not all(name in tools for name in must):
        return False
    if any_of and not any(name in tools for name in any_of):
        return False
    return True


def _check_facts(case: dict, answer: str) -> bool:
    must_all = case.get("must_include") or []
    must_any = case.get("must_include_any") or []
    if must_all and not all(_contains(answer, item) for item in must_all):
        return False
    if must_any and not any(_contains(answer, item) for item in must_any):
        return False
    return True


def _check_forbidden(case: dict, answer: str) -> list[str]:
    hits: list[str] = []
    for item in case.get("forbidden") or []:
        if item.startswith("re:"):
            if re.search(item[3:], answer):
                hits.append(item[3:])
        elif _contains(answer, item):
            hits.append(item)
    return hits


async def _run_case(graph, user: User, case: dict) -> CaseResult:
    result = CaseResult(
        case_id=case["id"],
        category=case.get("category", ""),
        query=case["query"],
        intent_expected=case.get("intent"),
        pending_expected=case.get("expect_pending"),
        tools_checked=bool(case.get("must_tools") or case.get("any_tools")),
        facts_checked=bool(
            case.get("must_include") or case.get("must_include_any")
        ),
    )
    async with SessionLocal() as db:
        conversation = await chat_repo.create_conversation(
            db, user_id=user.id, title=f"评测-{case['id']}"
        )
    config = {"configurable": {"thread_id": f"eval:{conversation.id}"}}
    started = time.perf_counter()
    try:
        await graph.ainvoke(
            {
                "messages": [HumanMessage(content=case["query"])],
                "intent": "",
                "pending_action": None,
                "parts": [],
                "steps": 0,
                "degraded": False,
            },
            config=config,
            context={
                "user_id": user.id,
                "conversation_id": conversation.id,
                "context_block": "",
            },
        )
        state = await graph.aget_state(config)
        values = (state.values if state else {}) or {}
        messages = values.get("messages", [])
        result.answer = _last_ai(messages)
        result.intent_actual = str(values.get("intent") or "")
        result.tools = [
            str(call.get("name"))
            for message in messages
            if isinstance(message, AIMessage)
            for call in (message.tool_calls or [])
        ]
        result.pending_actual = bool(
            state and state.tasks and state.tasks[0].interrupts
        )
    except Exception as exc:  # 单个用例失败不影响整体评测
        result.error = f"{type(exc).__name__}: {exc}"
    finally:
        result.latency_ms = (time.perf_counter() - started) * 1000
        await _cleanup(conversation.id)

    result.intent_ok = (
        result.intent_expected in (None, "")
        or result.intent_actual == result.intent_expected
    )
    result.tools_ok = _check_tools(case, result.tools)
    result.facts_ok = _check_facts(case, result.answer)
    result.forbidden_hits = _check_forbidden(case, result.answer)
    if result.pending_expected is not None:
        result.pending_ok = result.pending_actual == result.pending_expected
    return result


async def _cleanup(conversation_id: str) -> None:
    async with SessionLocal() as db:
        await db.execute(
            delete(PendingAction).where(
                PendingAction.conversation_id == conversation_id
            )
        )
        await db.execute(delete(Message).where(Message.conversation_id == conversation_id))
        await db.execute(delete(Conversation).where(Conversation.id == conversation_id))
        await db.commit()


def _percent(part: int, total: int) -> str:
    return f"{part / total:.1%}" if total else "0.0%"


def render_report(results: list[CaseResult]) -> str:
    total = len(results)
    passed = sum(item.passed for item in results)
    intent_cases = [item for item in results if item.intent_expected]
    intent_ok = sum(item.intent_ok for item in intent_cases)
    tools_cases = [item for item in results if item.tools_checked]
    tools_ok = sum(item.tools_ok for item in tools_cases)
    facts_cases = [item for item in results if item.facts_checked]
    facts_ok = sum(item.facts_ok for item in facts_cases)
    pending_cases = [item for item in results if item.pending_expected is not None]
    pending_ok = sum(item.pending_ok for item in pending_cases)
    violations = sum(len(item.forbidden_hits) for item in results)
    errors = sum(1 for item in results if item.error)
    latencies = sorted(item.latency_ms for item in results)
    avg_ms = sum(latencies) / total if total else 0.0
    p95_ms = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))] if latencies else 0.0

    by_category: dict[str, list[CaseResult]] = {}
    for item in results:
        by_category.setdefault(item.category, []).append(item)

    lines = [
        "# 回答质量评测报告",
        "",
        f"- 生成时间：{datetime.now(UTC).astimezone().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 用例数：{total}（`backend/benchmarks/golden_qa.jsonl`）",
        f"- 模型：{get_settings().llm_model}（真实端到端调用，不含检索层单测）",
        "- 评测方式：每个用例新建独立会话，跑完整 Agent 图后核对回答与状态，评测结束删除会话数据",
        "",
        "## 总览",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
        f"| 端到端通过率 | {passed}/{total}（{_percent(passed, total)}） |",
        f"| 路由准确率 | {intent_ok}/{len(intent_cases)}（{_percent(intent_ok, len(intent_cases))}） |",
        f"| 工具调用匹配率 | {tools_ok}/{len(tools_cases)}（{_percent(tools_ok, len(tools_cases))}） |",
        f"| 事实要点覆盖率 | {facts_ok}/{len(facts_cases)}（{_percent(facts_ok, len(facts_cases))}） |",
        f"| 审批触发正确率 | {pending_ok}/{len(pending_cases)}（{_percent(pending_ok, len(pending_cases))}） |",
        f"| 越权/敏感表述 | {violations} 处 |",
        f"| 异常用例 | {errors} 个 |",
        f"| 平均 / P95 延迟 | {avg_ms:.0f} ms / {p95_ms:.0f} ms |",
        "",
        "## 分类别",
        "",
        "| 类别 | 用例 | 通过 | 通过率 |",
        "|---|---:|---:|---:|",
    ]
    for category, items in sorted(by_category.items()):
        ok = sum(item.passed for item in items)
        lines.append(f"| {category} | {len(items)} | {ok} | {_percent(ok, len(items))} |")

    lines += [
        "",
        "## 逐条明细",
        "",
        "| 用例 | 查询 | 预期意图 | 实际意图 | 工具 | 结果 | 说明 |",
        "|---|---|---|---|---|---|---|",
    ]
    for item in results:
        status = "通过" if item.passed else "未通过"
        tools = ", ".join(item.tools) if item.tools else "无"
        query = item.query.replace("|", "\\|")
        expected = item.intent_expected or "-"
        answer_note = item.reason.replace("|", "\\|")
        if not item.passed and item.category != "security" and item.answer:
            excerpt = item.answer.replace("\n", " ").replace("|", "\\|")[:60]
            answer_note += f"｜回答：{excerpt}"
        lines.append(
            f"| {item.case_id} | {query} | {expected} | "
            f"{item.intent_actual or '-'} | {tools} | {status} | {answer_note} |"
        )

    lines += [
        "",
        "## 口径说明",
        "",
        "- 「事实要点」来自人工标注的订单号、状态、价格、政策关键词，不做语义相似度判定，避免主观打分。",
        "- 事实匹配会忽略空格与千分位逗号（如 `5,999` 与 `5999` 等价）。",
        "- 「越权/敏感表述」检查两类：未确认就宣称已执行（如“已取消”）、泄露 11 位手机号。",
        "- 安全类用例只检查是否泄露/越权，不强制路由意图（`intent: null`）。",
        "- 结果受 LLM 采样影响，重跑会有小幅波动；评测脚本只做黑盒调用，不修改业务代码。",
        "",
    ]
    return "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser(description="回答级端到端评测")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 条用例")
    parser.add_argument("--phone", default=DEFAULT_PHONE, help="评测使用的演示账号")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="报告输出路径")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.dashscope_api_key:
        print("未配置 DASHSCOPE_API_KEY，无法进行端到端评测。")
        return 1

    cases = _load_cases(args.limit)
    async with SessionLocal() as db:
        user = await db.scalar(
            select(User).where(User.phone_hash == hash_phone(args.phone))
        )
    if not user:
        print(f"未找到演示账号 {args.phone}，请先执行 seed_data --reset。")
        await dispose_engine()
        return 1

    graph = build_graph(checkpointer=MemorySaver())
    results: list[CaseResult] = []
    for index, case in enumerate(cases, start=1):
        print(f"[{index}/{len(cases)}] {case['id']} ...", end=" ", flush=True)
        result = await _run_case(graph, user, case)
        results.append(result)
        print("通过" if result.passed else f"未通过（{result.reason}）")

    report = render_report(results)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    print(f"\n报告已写入：{output}")
    await dispose_engine()
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    raise SystemExit(asyncio.run(main()))
