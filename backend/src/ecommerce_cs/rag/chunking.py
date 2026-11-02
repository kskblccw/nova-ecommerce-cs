"""中文友好的文本切分：按段落/句号聚合到目标长度。"""

import re

_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!\n])")


def split_text(text: str, chunk_size: int = 500, overlap: int = 80) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    sentences = [part for part in _SENTENCE_SPLIT.split(text) if part.strip()]
    chunks: list[str] = []
    current = ""

    for sentence in sentences:
        if len(current) + len(sentence) <= chunk_size:
            current += sentence
        else:
            if current:
                chunks.append(current.strip())
            if len(sentence) > chunk_size:
                start = 0
                while start < len(sentence):
                    chunks.append(sentence[start : start + chunk_size])
                    start += chunk_size - overlap
                current = ""
            else:
                current = sentence

    if current.strip():
        chunks.append(current.strip())

    # 相邻块补齐少量重叠，提升召回连续性
    if overlap > 0 and len(chunks) > 1:
        for index in range(1, len(chunks)):
            prefix = chunks[index - 1][-overlap:]
            if prefix and not chunks[index].startswith(prefix):
                chunks[index] = prefix + chunks[index]

    return chunks
