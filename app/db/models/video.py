from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base
from datetime import datetime, timezone


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[str] = mapped_column(primary_key=True)
    product_id: Mapped[str] = mapped_column(nullable=False)
    product_title: Mapped[str] = mapped_column(nullable=False)
    video_id: Mapped[str] = mapped_column(nullable=True)
    shop: Mapped[str] = mapped_column(nullable=False)
    image1: Mapped[str] = mapped_column(nullable=False)
    image2: Mapped[str] = mapped_column(nullable=True)
    image3: Mapped[str] = mapped_column(nullable=True)
    image4: Mapped[str] = mapped_column(nullable=True)
    prompt: Mapped[str] = mapped_column(nullable=False)
    video_url: Mapped[str] = mapped_column(nullable=True)
    thumbnail: Mapped[str] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(nullable=False)
    duration: Mapped[float] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))