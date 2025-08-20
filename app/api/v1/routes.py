import httpx
import requests
import os
import mimetypes
import asyncio
from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.orm import Session
from typing import List, Dict, Any
from app.core.constants import KLING_AI_TASK_STATUS_URL, KLING_AI_GENERATE_URL, ACCESS_KEY, SECRET_KEY, Shopify_Base_URL, X_Shopify_Access_Token
from app.core.constants import FILE_STATUS, STAGED_UPLOADS_CREATE, FILE_CREATE, FILE_UPDATE_ADD_PRODUCT
from app.core.utils import get_size_and_download, encode_jwt_token
from app.crud.video import create_video
from app.db.deps import get_db
from app.db.models.video import Video
from app.schema.video import VideoSummary, GenerateVideoRequest, VideoUploadRequest
from app.core.utils import get_thumbnail_from_url

router = APIRouter()

@router.get("/")
def root():
    return {"message": "Hello, World!"}

@router.get('/video', response_model=List[VideoSummary])
async def get_video(shop: str = Query(...), db: Session = Depends(get_db)):
    return db.query(Video).filter(Video.shop == shop).order_by(Video.created_at.desc()).all()

@router.delete('/video/{video_id}', status_code=204)
async def delete_video(video_id:str, shop: str = Query(...), token: str = Query(...), db: Session = Depends(get_db)):
    video = db.query(Video).filter(Video.id == video_id).first()

    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    mutation = """
    mutation fileDelete($fileIds: [ID!]!) {
      fileDelete(fileIds: $fileIds) {
        deletedFileIds
        userErrors { field message code }
      }
    }
    """
    url = f"https://{shop}/admin/api/2025-07/graphql.json"
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    resp = requests.post(url, json={"query": mutation, "variables": {"fileIds": [video.video_id]}}, headers=headers, timeout=30)
    
    db.delete(video)
    db.commit()

    return None

@router.put('/video/{video_id}', response_model=VideoSummary)
async def update_video(video_id: str, db: Session = Depends(get_db)):
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    headers = {
        "Authorization": f"Bearer {encode_jwt_token(ACCESS_KEY, SECRET_KEY)}"
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{KLING_AI_TASK_STATUS_URL}/{video_id}",
                headers=headers
            )
        response.raise_for_status()
        data = response.json()

        if data['data']['task_status'] == 'succeed':
            if video.status == 'processing':
                video.video_url = data['data']['task_result']['videos'][0]['url']
                video.status = 'completed'
                video.duration = data['data']['task_result']['videos'][0]['duration']
                video.thumbnail = get_thumbnail_from_url(video.video_url)

                db.commit()
                db.refresh(video)
        elif data['data']['task_status'] == 'failed':
            video.status = data['data']['task_status']

            db.commit()
            db.refresh(video)

        return video
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=str(exc))
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Error contacting third-party API: {str(exc)}")

@router.post("/video")
async def generate_video(body: GenerateVideoRequest, db: Session = Depends(get_db)):
    payload: Dict[str, Any] = {
        "image_list": [],
        "prompt": body.prompt,
        "aspect_ratio": "1:1"
    }
    headers = {
        "Authorization": f"Bearer {encode_jwt_token(ACCESS_KEY, SECRET_KEY)}"
    }

    for image in body.images:
        payload["image_list"].append({
            "image": image
        })

    images = [img_dict["image"] for img_dict in payload["image_list"]]

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                KLING_AI_GENERATE_URL,
                json=payload,
                headers=headers
            )
        response.raise_for_status()
        data = response.json()

        create_video(db, data['data']['task_id'], images, body.prompt, body.product_id, body.shop)

        return data
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=str(exc))
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Error contacting third-party API: {str(exc)}")

@router.get('/products')
async def get_products():
    try:
        headers = {
            "X-Shopify-Access-Token": X_Shopify_Access_Token
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{Shopify_Base_URL}/admin/api/2024-07/products.json?limit=250",
                headers=headers
            )
        response.raise_for_status()
        data = response.json()
        print(data)
        return data
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=str(exc))

async def gql(GQL_URL, HEADERS, client: httpx.AsyncClient, query: str, variables: Dict[str, Any]):
    r = await client.post(GQL_URL, headers=HEADERS, json={"query": query, "variables": variables})
    req_id = r.headers.get("X-Request-Id")
    if r.status_code >= 400:
        raise HTTPException(r.status_code, f"Shopify HTTP error: {r.text} (X-Request-Id: {req_id})")
    data = r.json()
    if "errors" in data:
        raise HTTPException(502, {"message": "Shopify GraphQL error", "errors": data["errors"], "x_request_id": req_id})
    return {"data": data["data"], "x_request_id": req_id}


async def wait_until_ready(GQL_URL, HEADERS, client: httpx.AsyncClient, file_id: str, timeout_s: int = 300):
    start = asyncio.get_event_loop().time()
    while True:
        d = await gql(GQL_URL, HEADERS, client, FILE_STATUS, {"id": file_id})
        status = d["data"]["node"]["fileStatus"]
        if status == "READY":
            return
        if status in ("FAILED", "CANCELLED"):
            raise HTTPException(502, f"Video processing failed with status {status}")
        if asyncio.get_event_loop().time() - start > timeout_s:
            raise HTTPException(504, f"Timed out waiting for READY (last status: {status})")
        await asyncio.sleep(1.2)

@router.post('/upload')
async def upload_video(payload: VideoUploadRequest, db: Session = Depends(get_db)):
    GQL_URL = f"https://{payload.shop}/admin/api/2024-07/graphql.json"

    HEADERS = {
        "X-Shopify-Access-Token": payload.token
    }

    video = db.query(Video).filter(Video.id == payload.video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    try:
        video.status = "uploading"
        db.commit()
        db.refresh(video)

        size, file_path = await get_size_and_download(payload.video_url)

        filename = os.path.basename(file_path) or "video.mp4"
        mime = mimetypes.guess_type(filename)[0] or "video/mp4"

        product_gid = f"{payload.product_id}"
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            # 1) stagedUploadsCreate
            variables: Dict[str, Any] = {
                "input": [{
                    "filename": filename,
                    "mimeType": mime,
                    "resource": "VIDEO",
                    "httpMethod": "POST",
                    "fileSize": str(size),
                }]
            }
            d1 = await gql(GQL_URL, HEADERS, client, STAGED_UPLOADS_CREATE, variables)
            su = d1["data"]["stagedUploadsCreate"]
            if su["userErrors"]:
                raise HTTPException(400, {"where": "stagedUploadsCreate", "userErrors": su["userErrors"], "x_request_id": d1["x_request_id"]})
            target = su["stagedTargets"][0]
            upload_url = target["url"]
            params = {p["name"]: p["value"] for p in target["parameters"]}  # includes key, policy, x-goog-*, etc.
            resource_url = target["resourceUrl"]

            print(resource_url)

            # 2) multipart POST (fields EXACTLY as provided + file)
            multipart = params.copy()
            # Some endpoints require 'Content-Type' field inside the form
            # multipart["Content-Type"] = mime
            with open(file_path, "rb") as f:
                files = {**{k: (None, v) for k, v in multipart.items()}, "file": (filename, f, mime)}
                r = await client.post(upload_url, files=files)
                if r.status_code not in (204, 201, 200):
                    raise HTTPException(502, f"Upload to staged target failed: {r.status_code} {r.text}")

            # 3) fileCreate using the staged resourceUrl
            d2 = await gql(GQL_URL, HEADERS, client, FILE_CREATE, {
                "files": [{
                    "contentType": "VIDEO",
                    "originalSource": resource_url,
                    **({})
                }]
            })
            fc = d2["data"]["fileCreate"]
            if fc["userErrors"]:
                raise HTTPException(400, {"where": "fileCreate", "userErrors": fc["userErrors"], "x_request_id": d2["x_request_id"]})
            file_id = fc["files"][0]["id"]

            print("file_id", file_id)

            # 4) wait until READY
            await wait_until_ready(GQL_URL, HEADERS, client, file_id)

            # 5) attach to product
            d3 = await gql(GQL_URL, HEADERS, client, FILE_UPDATE_ADD_PRODUCT, {
                "files": [{"id": file_id, "referencesToAdd": [product_gid]}]
            })
            fu = d3["data"]["fileUpdate"]
            if fu["userErrors"]:
                raise HTTPException(400, {"where": "fileUpdate", "userErrors": fu["userErrors"], "x_request_id": d3["x_request_id"]})

            try:
                os.remove(file_path)
            except Exception:
                pass

            video.status = "uploaded"
            video.video_id = file_id
            db.commit()
            db.refresh(video)

            return {"ok": True, "video_id": file_id, "attached_to": product_gid}
    except Exception as e:
        video.status = "completed"
        db.commit()
        db.refresh(video)
        raise e