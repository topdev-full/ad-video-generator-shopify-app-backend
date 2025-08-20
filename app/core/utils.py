import time
import os
import cv2
import requests
import httpx
import tempfile
import mimetypes
import base64
from jwt import encode

def encode_jwt_token(ak: str, sk: str) -> str:
    headers = {
        "alg": "HS256",
        "typ": "JWT"
    }
    payload = {
        "iss": ak,
        "exp": int(time.time()) + 10 * 24 * 3600, # The valid time, in this example, represents the current time+1800s(30min)
        "nbf": int(time.time()) - 10 * 24 * 3600  # The time when it starts to take effect, in this example, represents the current time minus 5s
    }
    token = encode(payload, sk, headers=headers)
    return token

async def get_size_and_download(url: str) -> tuple[int, str]:
    # Returns (size_bytes, temp_file_path)
    async with httpx.AsyncClient(follow_redirects=True, timeout=60) as c:
        # Try HEAD for size
        head = await c.head(url)
        cl = head.headers.get("Content-Length")
        if cl is None or int(cl) == 0:
            # Fallback: stream and count
            resp = await c.get(url)
            resp.raise_for_status()
            fd, path = tempfile.mkstemp(suffix=".mp4")
            size = 0
            with os.fdopen(fd, "wb") as f:
                for chunk in resp.iter_bytes():
                    if chunk:
                        f.write(chunk)
                        size += len(chunk)
            return size, path
        # If we have size, still need the bytes
        resp = await c.get(url)
        resp.raise_for_status()
        fd, path = tempfile.mkstemp(suffix=".mp4")
        with os.fdopen(fd, "wb") as f:
            for chunk in resp.iter_bytes():
                if chunk:
                    f.write(chunk)
        return int(cl), path

def get_thumbnail_from_url(url: str) -> str:
    print(url)
    # Step 1: Stream the video file
    resp = requests.get(url, stream=True)
    if resp.status_code != 200:
        raise Exception(f"Failed to download video: {resp.status_code}")

    # Step 2: Save video temporarily
    temp_path = "./temp_video.mp4"
    with open(temp_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)

    # Step 3: Load video and extract first frame
    cap = cv2.VideoCapture(temp_path)
    success, frame = cap.read()
    cap.release()

    if not success:
        raise Exception("Failed to read frame from video")

    # Step 4: Encode frame to JPEG
    success, buffer = cv2.imencode(".jpg", frame)
    if not success:
        raise Exception("Failed to encode frame to JPEG")

    # Step 5: Convert to base64
    base64_image = base64.b64encode(buffer).decode("utf-8")
    return base64_image