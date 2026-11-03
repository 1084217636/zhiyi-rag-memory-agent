import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.controllers.chat_controller import chat_stream
from app.core.exceptions import BizError
from app.schemas.chat_schema import ChatStreamRequest


@pytest.mark.asyncio
async def test_foreign_conversation_rejected_before_streaming_headers():
    owner_id = uuid.uuid4()
    foreign_conversation = uuid.uuid4()
    body = ChatStreamRequest(conversation_id=foreign_conversation, message="你好")
    with patch(
        "app.controllers.chat_controller.ConversationService.get_owned",
        new_callable=AsyncMock,
        side_effect=BizError("会话不存在", code=4002, status_code=404),
    ) as get_owned:
        with pytest.raises(BizError) as error:
            await chat_stream(body, SimpleNamespace(id=owner_id), object())
    assert error.value.status_code == 404
    get_owned.assert_awaited_once_with(owner_id, foreign_conversation)
