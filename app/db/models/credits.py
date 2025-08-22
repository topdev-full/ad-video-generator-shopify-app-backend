from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base
from datetime import datetime, timezone

class Credits(Base):
    __tablename__ = "credits"
    
    shop_name: Mapped[str] = mapped_column(primary_key=True)
    extra_credit: Mapped[int] = mapped_column(default=0)
    monthly_credit: Mapped[int] = mapped_column(default=0)
    subscription_type: Mapped[int] = mapped_column(nullable=True)
    subscription_expired: Mapped[datetime] = mapped_column(nullable=True)