import os
import secrets
from pathlib import Path

try:
    import boto3
except Exception:
    boto3 = None

class Storage:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.endpoint = os.getenv('S3_ENDPOINT_URL', '').strip()
        self.bucket = os.getenv('S3_BUCKET', '').strip()
        self.access_key = os.getenv('S3_ACCESS_KEY_ID', '').strip()
        self.secret_key = os.getenv('S3_SECRET_ACCESS_KEY', '').strip()
        self.public_url = os.getenv('S3_PUBLIC_URL', '').rstrip('/')
        self.region = os.getenv('S3_REGION', 'auto').strip() or 'auto'
        self.enabled = bool(self.endpoint and self.bucket and self.access_key and self.secret_key and boto3)
        self.client = None
        if self.enabled:
            self.client = boto3.client(
                's3', endpoint_url=self.endpoint, region_name=self.region,
                aws_access_key_id=self.access_key, aws_secret_access_key=self.secret_key,
            )

    def save_image(self, tenant_id: int, filename: str, data: bytes) -> str:
        ext = Path(filename).suffix.lower()
        if ext not in {'.png', '.jpg', '.jpeg', '.webp'}:
            raise ValueError('Formato não permitido')
        if len(data) > 2_000_000:
            raise ValueError('Arquivo maior que 2 MB')
        key = f'tenants/{tenant_id}/logo-{secrets.token_hex(8)}{ext}'
        content_type = {
            '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'
        }[ext]
        if self.enabled:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
            if self.public_url:
                return f'{self.public_url}/{key}'
            return f'{self.endpoint.rstrip("/")}/{self.bucket}/{key}'
        local_name = key.replace('/', '-')
        (self.base_dir / local_name).write_bytes(data)
        return f'/static/uploads/{local_name}'
