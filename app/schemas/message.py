from __future__ import annotations
import uuid
from datetime import datetime
from pydantic import BaseModel

class MessageOut(BaseModel):
    id: uuid.UUID
    username: str
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}

class FeedResponse(BaseModel):
    items: list[MessageOut]
    total: int
