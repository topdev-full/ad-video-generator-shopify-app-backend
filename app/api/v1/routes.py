import httpx
import requests
import os
import mimetypes
import asyncio
import stripe
from fastapi import APIRouter, HTTPException, Depends, Query, Request
from sqlalchemy.orm import Session
from typing import List, Dict, Any
from app.core.constants import KLING_AI_TASK_STATUS_URL, KLING_AI_GENERATE_URL, ACCESS_KEY, SECRET_KEY, STRIPE_SECRET_KEY
from app.core.constants import FILE_STATUS, STAGED_UPLOADS_CREATE, FILE_CREATE, FILE_UPDATE_ADD_PRODUCT, STRIPE_WEBHOOK_SECRET
from app.core.utils import get_size_and_download, encode_jwt_token
from app.crud.video import create_video
from app.db.deps import get_db
from app.db.models.video import Video
from app.db.models.credits import Credits
from app.schema.video import ShopNamePayload, VideoSummary, GenerateVideoRequest, VideoUploadRequest, CreateSessionRequest
from app.core.utils import get_thumbnail_from_url, checkIfAvailable, updateCredits
from datetime import datetime, timedelta

router = APIRouter()
stripe.api_key = STRIPE_SECRET_KEY

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
    if not checkIfAvailable(body.shop):
        raise HTTPException(status_code=403, detail=f"Not Enough Credits. Charge credits.")

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

        updateCredits(body.shop)
        return data
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=str(exc))
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Error contacting third-party API: {str(exc)}")

async def gql(GQL_URL: str, HEADERS: Any, client: httpx.AsyncClient, query: str, variables: Dict[str, Any]):
    r = await client.post(GQL_URL, headers=HEADERS, json={"query": query, "variables": variables})
    req_id = r.headers.get("X-Request-Id")
    if r.status_code >= 400:
        raise HTTPException(r.status_code, f"Shopify HTTP error: {r.text} (X-Request-Id: {req_id})")
    data = r.json()
    if "errors" in data:
        raise HTTPException(502, {"message": "Shopify GraphQL error", "errors": data["errors"], "x_request_id": req_id})
    return {"data": data["data"], "x_request_id": req_id}

async def wait_until_ready(GQL_URL: str, HEADERS: Any, client: httpx.AsyncClient, file_id: str, timeout_s: int = 300):
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

@router.post('/subscription')
async def create_subscription(db: Session = Depends(get_db)):
    try:
        pass
    except Exception as e:
        raise e

@router.post('/stripe-hook')
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    sig_header = request.headers.get("stripe-signature")
    body = await request.body()
    try:
        event = stripe.Webhook.construct_event(body, sig_header, STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    type_ = event["type"]
    data = event["data"]["object"]

    # On checkout completed, you can link customer to user
    if type_ == "checkout.session.completed":
        shop_id = data.get("client_reference_id")
        print("shop_id:", shop_id)
        line_items = stripe.checkout.Session.list_line_items(data["id"], limit=100)
        print("line_items:", line_items['data'])
        price_id, product_id, quantity, amount = line_items['data'][0]['price']['id'], line_items['data'][0]['price']['product'], line_items['data'][0]['quantity'], line_items['data'][0]['price']['unit_amount']
        print("line_items:", price_id, product_id, quantity, amount)
        if product_id == "prod_Su4INfJofTlANV": # extra credit
            credits = db.query(Credits).filter(Credits.shop_name == shop_id).first()
            if not credits:
                credits = Credits(
                    shop_name=shop_id,
                    extra_credit=quantity,
                )
                db.add(credits)
                db.commit()
            else:
                credits.extra_credit = credits.extra_credit + quantity
                db.commit()
        else:
            credits = db.query(Credits).filter(Credits.shop_name == shop_id).first()
            if not credits:
                credits = Credits(
                    shop_name=shop_id,
                    monthly_credit=amount/140,
                    subscription_type=(1 if amount == 14000 else (2 if amount == 35000 else 3)),
                    subscription_expired=datetime.now()+timedelta(days=30)
                )
                db.add(credits)
                db.commit()
            else:
                credits.monthly_credit = amount / 140
                credits.subscription_type=(1 if amount == 14000 else (2 if amount == 35000 else 3))
                credits.subscription_expired=datetime.now()+timedelta(days=30)
                db.commit()

@router.post('/create-checkout-session')
async def create_checkout_session(payload: CreateSessionRequest, db: Session = Depends(get_db)):
    try:
        line_item = None
        if payload.plan == "template-1":
            line_item = stripe.checkout.Session.CreateParamsLineItem({
                "price": "price_1RyG9396qwFkAOsoc3dJW8kz",
                "quantity": 1
            })
        elif payload.plan == "template-2":
            line_item = stripe.checkout.Session.CreateParamsLineItem({
                "price": "price_1RyG9W96qwFkAOsomQlAs3MV",
                "quantity": 1
            })
        elif payload.plan == "template-3":
            line_item = stripe.checkout.Session.CreateParamsLineItem({
                "price": "price_1RyG9o96qwFkAOsojank6DtR",
                "quantity": 1
            })
        else:
            line_item = stripe.checkout.Session.CreateParamsLineItem({
                "price": "price_1RyGAC96qwFkAOsorujppUrp",
                "quantity": payload.credits
            })
        session = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=['card'],
            line_items=[line_item],
            success_url=payload.redirectUrl,
            cancel_url=payload.redirectUrl,
            client_reference_id=payload.shop,
            customer_creation='if_required',
            allow_promotion_codes=False
        )
        return {"id": session.id}
    except Exception as e:
        raise e

@router.post('/credits')
async def get_credits(payload: ShopNamePayload, db: Session = Depends(get_db)):
    credits = db.query(Credits).filter(Credits.shop_name == payload.shop).first()

    if not credits:
        return {
            "extra_credit": 0,
            "monthly_credit": 0,
            "subscription_type": -1,
            "subscription_expired": None,
            "active_subscription": False
        }

    return {
        "extra_credit": credits.extra_credit,
        "monthly_credit": credits.monthly_credit,
        "subscription_type": credits.subscription_type,
        "subscription_expired": credits.subscription_expired,
        "active_subscription": datetime.now() <= credits.subscription_expired if credits.subscription_expired else False
    }

@router.post('/expire-subscription')
async def expire_subscription(db: Session = Depends(get_db)):
    try:
        pass
    except Exception as e:
        raise e