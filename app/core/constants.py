import os
from dotenv import load_dotenv

load_dotenv()

KLING_AI_API_BASE_URL = os.getenv("KLING_AI_API_URL") or "https://api-singapore.klingai.com"
KLING_AI_GENERATE_URL = f"{KLING_AI_API_BASE_URL}/v1/videos/multi-image2video"
KLING_AI_TASK_STATUS_URL = f"{KLING_AI_API_BASE_URL}/v1/videos/multi-image2video"
ACCESS_KEY = os.getenv("ACCESS_KEY") or ""
SECRET_KEY = os.getenv("SECRET_KEY") or ""
SQLALCHEMY_DATABASE_URL = os.getenv("SQLALCHEMY_DATABASE_URL") or ""
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY") or ""
CLIENT_URL = os.getenv("CLIENT_URL") or ""
STRIPE_WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET") or ""

STAGED_UPLOADS_CREATE = """
mutation StagedUploadsCreate($input: [StagedUploadInput!]!) {
  stagedUploadsCreate(input: $input) {
    stagedTargets { url resourceUrl parameters { name value } }
    userErrors { field message }
  }
}
"""

FILE_CREATE = """
mutation FileCreate($files: [FileCreateInput!]!) {
  fileCreate(files: $files) {
    files { id fileStatus }
    userErrors { field message }
  }
}
"""

FILE_STATUS = """
query FileStatus($id: ID!) {
  node(id: $id) { ... on Video { id fileStatus } }
}
"""

FILE_UPDATE_ADD_PRODUCT = """
mutation FileUpdate($files: [FileUpdateInput!]!) {
  fileUpdate(files: $files) {
    files { id }
    userErrors { field message code }
  }
}
"""