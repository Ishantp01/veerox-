from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class HelpDeskMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=4000)


class HelpDeskChatIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    # Prior turns of this widget session, oldest first. Kept client-side —
    # there is no server-side persistence for help-desk chats.
    history: list[HelpDeskMessage] = Field(default_factory=list)


class HelpDeskChatOut(BaseModel):
    reply: str
