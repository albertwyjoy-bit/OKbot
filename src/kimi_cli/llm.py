from typing import NamedTuple

from kosong.base.chat_provider import ChatProvider


class LLM(NamedTuple):
    chat_provider: ChatProvider
    max_context_size: int
