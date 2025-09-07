from sqlalchemy.orm import Session
from app.db.models.video import Video
from typing import List

def create_video(db: Session, id: str, images: List[str], prompt: str, product_id: str, product_title: str, shop: str):
    video = Video(
        id = id,
        product_id = product_id,
        product_title = product_title,
        shop = shop,
        image1 = images[0] if len(images) > 0 else "",
        image2 = images[1] if len(images) > 1 else "",
        image3 = images[2] if len(images) > 2 else "",
        image4 = images[3] if len(images) > 3 else "",
        prompt = prompt,
        video_url = "",
        status = "processing",
        duration = 5,
    )
    
    db.add(video)
    db.commit()
    db.refresh(video)
    return video