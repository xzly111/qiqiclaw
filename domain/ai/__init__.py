"""
Stub for domain.ai — qiqiclaw 移植后不使用旧 AI/向量化能力。
保留 import 兼容：所有调用 noop / 返回空，或抛 NotImplementedError 让上层自然 fallback。

virtual_fs 内调用 VectorDBService 来做"按向量检索/删向量"，
这里让相关方法静默成功（搜索退化为传统的文件名/全文匹配）。
"""
from typing import Any, Dict, List, Optional


DEFAULT_VECTOR_DIMENSION = 768
VECTOR_COLLECTION_NAME = "qiqiclaw_files_vec"
FILE_COLLECTION_NAME = "qiqiclaw_files"

ABILITIES: List[Dict[str, Any]] = []


class MissingModelError(Exception):
    pass


def normalize_capabilities(*args: Any, **kwargs: Any) -> List[str]:
    return []


async def chat_completion(*args: Any, **kwargs: Any) -> str:
    raise MissingModelError("AI features are disabled in qiqiclaw build")


async def chat_completion_stream(*args: Any, **kwargs: Any):
    raise MissingModelError("AI features are disabled in qiqiclaw build")


async def describe_image_base64(*args: Any, **kwargs: Any) -> str:
    return ""


async def get_text_embedding(*args: Any, **kwargs: Any) -> List[float]:
    return []


async def rerank_texts(*args: Any, **kwargs: Any) -> List[Any]:
    return []


class _NullProviderService:
    async def list_providers(self, *a: Any, **kw: Any) -> List[Any]:
        return []

    async def get(self, *a: Any, **kw: Any) -> None:
        return None


provider_service = _NullProviderService()


class VectorDBService:
    """No-op vector DB. virtual_fs 用来删除/查询向量条目，全部静默成功。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def initialize(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def has_collection(self, *args: Any, **kwargs: Any) -> bool:
        return False

    async def delete(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def delete_by_filter(self, *args: Any, **kwargs: Any) -> int:
        return 0

    async def upsert(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def search(self, *args: Any, **kwargs: Any) -> List[Any]:
        return []

    async def query(self, *args: Any, **kwargs: Any) -> List[Any]:
        return []

    async def drop(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def close(self, *args: Any, **kwargs: Any) -> None:
        return None


class AIProviderService:
    @staticmethod
    async def list(*args: Any, **kwargs: Any) -> List[Any]:
        return []

    @staticmethod
    async def get(*args: Any, **kwargs: Any) -> None:
        return None


class VectorDBConfigManager:
    @staticmethod
    async def get(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        return {}

    @staticmethod
    async def set(*args: Any, **kwargs: Any) -> None:
        return None


class BaseVectorProvider:
    pass


class MilvusLiteProvider(BaseVectorProvider):
    type = "milvus_lite"


class MilvusServerProvider(BaseVectorProvider):
    type = "milvus_server"


class QdrantProvider(BaseVectorProvider):
    type = "qdrant"


def list_providers() -> List[Dict[str, Any]]:
    return []


def get_provider_class(*args: Any, **kwargs: Any):
    return BaseVectorProvider


def get_provider_entry(*args: Any, **kwargs: Any):
    return None


# Pydantic placeholders for types module compatibility
from pydantic import BaseModel


class AIDefaultsUpdate(BaseModel):
    pass


class AIModelCreate(BaseModel):
    pass


class AIModelUpdate(BaseModel):
    pass


class AIProviderCreate(BaseModel):
    pass


class AIProviderUpdate(BaseModel):
    pass


class VectorDBConfigPayload(BaseModel):
    pass


__all__ = [
    "MissingModelError",
    "chat_completion",
    "chat_completion_stream",
    "describe_image_base64",
    "get_text_embedding",
    "provider_service",
    "rerank_texts",
    "AIProviderService",
    "VectorDBService",
    "VectorDBConfigManager",
    "DEFAULT_VECTOR_DIMENSION",
    "VECTOR_COLLECTION_NAME",
    "FILE_COLLECTION_NAME",
    "BaseVectorProvider",
    "MilvusLiteProvider",
    "MilvusServerProvider",
    "QdrantProvider",
    "list_providers",
    "get_provider_entry",
    "get_provider_class",
    "ABILITIES",
    "normalize_capabilities",
    "AIDefaultsUpdate",
    "AIModelCreate",
    "AIModelUpdate",
    "AIProviderCreate",
    "AIProviderUpdate",
    "VectorDBConfigPayload",
]
