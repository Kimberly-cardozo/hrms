from typing import Literal

from pydantic import BaseModel, Field


class ChatSessionCreate(BaseModel):
    title: str = Field(min_length=3, max_length=120)


class ChatMessageCreate(BaseModel):
    role: str = Field(min_length=3, max_length=20)
    content: str = Field(min_length=1, max_length=4000)


class SQLChatHistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)


class SQLChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[SQLChatHistoryMessage] = Field(default_factory=list, max_length=8)


class ActionChatRequest(BaseModel):
    message: str | None = Field(default=None, min_length=1, max_length=2000)
    history: list[SQLChatHistoryMessage] = Field(default_factory=list, max_length=8)
    confirmation_token: str | None = Field(default=None, min_length=20, max_length=4096)


class ActionChatHistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)


class ActionChatRequest(BaseModel):
    message: str | None = Field(default=None, min_length=1, max_length=2000)
    history: list[ActionChatHistoryMessage] = Field(default_factory=list, max_length=8)
    confirmation_token: str | None = Field(default=None, min_length=20, max_length=4096)
