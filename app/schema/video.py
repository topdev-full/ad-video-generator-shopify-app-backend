from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class VideoSummary(BaseModel):
    id: str
    product_id: str
    product_title: str
    video_url: Optional[str]
    status: str
    duration: float
    thumbnail: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True

class GenerateVideoRequest(BaseModel):
    prompt: str
    product_id: str
    product_title: str
    images: List[str]
    shop: str

class VideoUploadRequest(BaseModel):
    shop: str
    token: str
    video_id: str
    video_url: str
    product_id: str
    product_title: str

class CreateSessionRequest(BaseModel):
    shop: str
    plan: str
    credits: int
    redirectUrl: str

class ShopNamePayload(BaseModel):
    shop: str