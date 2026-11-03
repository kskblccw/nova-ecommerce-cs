"""回答文本净化：去掉工具调用前的旁白，拦截未确认的成功声明。"""

from __future__ import annotations

import re

NARRATION_RE = re.compile(
    r"^(?:正在|好的[，,]\s*(?:我)?(?:正在|先|马上|这就)?|我(?:先|来)|让我|稍等|马上|这就)"
    r".{0,6}(?:查询|检索|核对|处理|整理|查找|确认|为您|帮您|看一下)"
    r".{0,24}[。！!，,\s]*$"
)

# 只匹配“助手已经替用户做了写操作”的说法：
# 必须有第一人称或“已为您/已帮您”，避免误伤“退款已提交至支付渠道”这类流程描述。
MUTATION_CLAIM_RE = re.compile(
    r"我(?:已经|已)?(?:为您|帮您)?(?:提交|申请|办理|修改|更新|发起|取消|完成|生成)"
    r"|已(?:为您|帮您)(?:提交|申请|办理|修改|更新|发起|取消|完成|生成)"
)

UNSUPPORTED_CLAIM_HINT = (
    "抱歉，我还没有执行该操作。所有变更都需要您在确认弹窗中核对后才会生效，"
    "请重新发送指令，我会先生成待确认操作。"
)

PENDING_HINT = "已为您准备好本次操作，请在确认弹窗中核对信息后再执行。"

DEGRADED_MESSAGE = (
    "抱歉，智能客服当前繁忙，暂时无法完成本次查询。"
    "请稍后重试，或发送「转人工」联系人工客服（热线 400-888-8888）。"
)


def strip_narration(text: str) -> str:
    """逐行去掉“正在查询…”这类工具前置旁白，保留真实业务状态。"""
    content = (text or "").strip()
    if not content:
        return ""
    kept: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped and NARRATION_RE.match(stripped):
            continue
        kept.append(line)
    cleaned = "\n".join(kept).strip()
    return cleaned


def sanitize_answer(text: str) -> str:
    """返回净化后的文本；如果整段都是工具前置旁白则返回空串。"""
    return strip_narration(text)


def sanitize_pending_answer(text: str) -> str:
    cleaned = strip_narration(text)
    if not cleaned:
        return PENDING_HINT
    if MUTATION_CLAIM_RE.search(cleaned):
        return PENDING_HINT
    return cleaned
