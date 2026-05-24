from __future__ import annotations
import uuid
from datetime import datetime
from pydantic import BaseModel

class UserOut(BaseModel):
    id: uuid.UUID
    username: str
    created_at: datetime

    model_config = {"from_attributes": True}
