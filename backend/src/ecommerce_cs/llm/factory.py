"""LLM 与 Embedding 工厂：统一超时、重试与缓存。"""

from functools import lru_cache

import httpx

from ecommerce_cs.core.settings import get_settings


@lru_cache
def get_llm(temperature: float = 0.3):
    from langchain_openai import ChatOpenAI

    settings = get_settings()
    return ChatOpenAI(
        model=settings.llm_model,
        temperature=temperature,
        base_url=settings.dashscope_base_url,
        api_key=settings.dashscope_api_key or "placeholder-key",  # type: ignore[arg-type]
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )


class DashScopeEmbeddings:
    """DashScope 原生 embedding 接口（异步、分批、带重试）。"""

    def __init__(self, model: str, api_key: str, dim: int = 1024):
        self.model = model
        self.api_key = api_key
        self.dim = dim
        self.url = (
            "https://dashscope.aliyuncs.com/api/v1/services/embeddings/"
            "text-embedding/text-embedding"
        )

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        results: list[list[float]] = []
        batch_size = 10
        async with httpx.AsyncClient(timeout=40) as client:
            for start in range(0, len(texts), batch_size):
                batch = texts[start : start + batch_size]
                for attempt in range(3):
                    try:
                        response = await client.post(
                            self.url,
                            json={"model": self.model, "input": {"texts": batch}},
                            headers={"Authorization": f"Bearer {self.api_key}"},
                        )
                        response.raise_for_status()
                        payload = response.json()
                        results.extend(
                            item["embedding"] for item in payload["output"]["embeddings"]
                        )
                        break
                    except httpx.HTTPError:
                        if attempt == 2:
                            raise
                        continue
        return results

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self.embed_documents([text])
        return vectors[0] if vectors else []


@lru_cache
def get_embeddings() -> DashScopeEmbeddings:
    settings = get_settings()
    return DashScopeEmbeddings(
        model=settings.embedding_model,
        api_key=settings.dashscope_api_key,
        dim=settings.embedding_dim,
    )
