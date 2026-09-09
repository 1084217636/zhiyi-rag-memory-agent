import unittest
from unittest.mock import AsyncMock, patch

from app.core.llm.client import LLMClient, _get_shared_client


class DeepSeekStructuredChatTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_client_is_scoped_to_current_event_loop(self):
        first = _get_shared_client()
        self.assertIs(first, _get_shared_client())

    async def test_v4_non_stream_chat_disables_thinking(self):
        client = LLMClient("https://api.deepseek.com", "test-key", "deepseek-v4-flash")
        response = {
            "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
            "usage": {},
        }
        with patch(
            "app.core.llm.client._post_with_retry", AsyncMock(return_value=response)
        ) as post:
            result = await client.chat([{"role": "user", "content": "返回 JSON"}])

        self.assertEqual(result, "{}")
        self.assertEqual(post.await_args.kwargs["json"]["thinking"], {"type": "disabled"})

    async def test_empty_final_content_is_an_error(self):
        client = LLMClient("https://api.deepseek.com", "test-key", "deepseek-v4-flash")
        response = {
            "choices": [{"finish_reason": "length", "message": {"content": ""}}],
            "usage": {},
        }
        with patch(
            "app.core.llm.client._post_with_retry", AsyncMock(return_value=response)
        ):
            with self.assertRaisesRegex(RuntimeError, "finish_reason=length"):
                await client.chat([{"role": "user", "content": "返回 JSON"}])


if __name__ == "__main__":
    unittest.main()
