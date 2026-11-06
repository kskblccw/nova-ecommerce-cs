"""单元测试公共夹具。"""

import pytest

from ecommerce_cs.rag import milvus_store


@pytest.fixture(autouse=True)
def _disable_milvus():
    """单测默认关闭 Milvus：避免在仓库里生成数据文件，也保证用例不依赖向量库。"""
    milvus_store.reset()
    milvus_store.disable()
    yield
    milvus_store.reset()
