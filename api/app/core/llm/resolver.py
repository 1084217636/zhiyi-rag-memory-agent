"""根据用户的默认模型配置构建 LLMClient。"""
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import BizError
from app.core.llm.client import LLMClient
from app.core.security import decrypt_secret
from app.repositories.model_config_repository import ModelConfigRepository

_TYPE_LABEL = {
    "chat": "对话",
    "multimodal": "多模态",
    "embedding": "Embedding",
    "rerank": "Rerank",
}


def _local_dev_client(type_: str) -> LLMClient | None:
    """从本地评测环境变量构建 development fallback；线上环境绝不启用。"""
    if settings.app_env.lower() != "development":
        return None
    if type_ == "chat":
        base_url, api_key, model_name = (
            settings.eval_chat_base_url,
            settings.eval_chat_key,
            settings.eval_chat_model,
        )
    elif type_ == "embedding":
        base_url, api_key, model_name = (
            settings.eval_embed_base_url,
            settings.eval_embed_key,
            settings.eval_embed_model,
        )
    else:
        return None
    if not (base_url and api_key and model_name):
        return None
    return LLMClient(base_url=base_url, api_key=api_key, model_name=model_name)


async def get_client_for_type(
    session: AsyncSession, user_id: uuid.UUID, type_: str
) -> LLMClient:
    """取用户某类型的默认模型配置，构建 LLMClient。无默认则报错。"""
    configs = await ModelConfigRepository(session).list_by_user(user_id, type_)
    if not configs:
        local_client = _local_dev_client(type_)
        if local_client is not None:
            return local_client
        label = _TYPE_LABEL.get(type_, type_)
        raise BizError(f"未配置{label}模型，请先在模型配置中添加", code=2010)
    # 优先默认配置，否则取第一个
    config = next((c for c in configs if c.is_default), configs[0])
    return LLMClient(
        base_url=config.base_url,
        api_key=decrypt_secret(config.api_key_encrypted),
        model_name=config.model_name,
    )


async def get_optional_client_for_type(
    session: AsyncSession, user_id: uuid.UUID, type_: str
) -> LLMClient | None:
    """同上，但无配置时返回 None（用于 rerank 等可选能力）。"""
    configs = await ModelConfigRepository(session).list_by_user(user_id, type_)
    if not configs:
        return None
    config = next((c for c in configs if c.is_default), configs[0])
    return LLMClient(
        base_url=config.base_url,
        api_key=decrypt_secret(config.api_key_encrypted),
        model_name=config.model_name,
    )
