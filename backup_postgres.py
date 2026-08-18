import os, subprocess, gzip, shutil
from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from dotenv import load_dotenv
load_dotenv()

DATABASE_URL=os.getenv('DATABASE_URL','').strip()
BACKUP_DIR=Path(os.getenv('BACKUP_DIR','./backups'))
RETENTION=int(os.getenv('BACKUP_RETENTION_DAYS','14'))
PREFIX=os.getenv('BACKUP_S3_PREFIX','backups').strip('/')

if not DATABASE_URL.startswith(('postgresql://','postgres://')):
    raise SystemExit('BACKUP: DATABASE_URL PostgreSQL não configurada.')
BACKUP_DIR.mkdir(parents=True, exist_ok=True)
stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
out=BACKUP_DIR/f'barbersaas-{stamp}.sql.gz'

with gzip.open(out,'wb') as gz:
    p=subprocess.run(['pg_dump','--no-owner','--no-privileges',DATABASE_URL],stdout=gz,stderr=subprocess.PIPE)
if p.returncode != 0:
    out.unlink(missing_ok=True)
    raise SystemExit('pg_dump falhou: '+p.stderr.decode(errors='replace'))

# upload opcional S3/R2
endpoint=os.getenv('S3_ENDPOINT_URL','').strip(); bucket=os.getenv('S3_BUCKET','').strip()
if endpoint and bucket:
    import boto3
    client=boto3.client('s3',endpoint_url=endpoint,aws_access_key_id=os.getenv('S3_ACCESS_KEY_ID'),aws_secret_access_key=os.getenv('S3_SECRET_ACCESS_KEY'),region_name=os.getenv('S3_REGION','auto'))
    key=f'{PREFIX}/{out.name}'
    client.upload_file(str(out),bucket,key)
    print('Backup enviado:',key)

cutoff=datetime.now(timezone.utc)-timedelta(days=RETENTION)
for f in BACKUP_DIR.glob('barbersaas-*.sql.gz'):
    if datetime.fromtimestamp(f.stat().st_mtime,timezone.utc)<cutoff:
        f.unlink(missing_ok=True)
print('Backup criado:',out)
