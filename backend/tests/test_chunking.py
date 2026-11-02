"""文本切分与稳定文档 ID 测试。"""

from ecommerce_cs.rag.chunking import split_text
from ecommerce_cs.rag.indexer import _system_id


def test_short_text_not_split() -> None:
    assert split_text("短文本") == ["短文本"]


def test_long_text_split_under_limit() -> None:
    text = "这是第一句。" * 120
    chunks = split_text(text, chunk_size=200, overlap=20)
    assert len(chunks) > 1
    assert all(len(chunk) <= 230 for chunk in chunks)


def test_empty_text() -> None:
    assert split_text("") == []
    assert split_text("   ") == []


def test_system_document_ids_are_stable() -> None:
    assert _system_id("product", 1) == _system_id("product", 1)
    assert _system_id("product", 1) != _system_id("product", 2)
    assert len(_system_id("policy", "aftersale")) == 32
