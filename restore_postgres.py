import os, sys, gzip, subprocess
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
DATABASE_URL=os.getenv('DATABASE_URL','').strip()
if len(sys.argv)!=2:
    raise SystemExit('Uso: python restore_postgres.py caminho/backup.sql.gz')
if not DATABASE_URL.startswith(('postgresql://','postgres://')):
    raise SystemExit('DATABASE_URL PostgreSQL não configurada.')
path=Path(sys.argv[1])
if not path.exists(): raise SystemExit('Backup não encontrado.')
print('ATENÇÃO: restauração altera o banco apontado por DATABASE_URL.')
if os.getenv('CONFIRM_RESTORE')!='YES':
    raise SystemExit('Defina CONFIRM_RESTORE=YES para confirmar conscientemente.')
with gzip.open(path,'rb') as src:
    p=subprocess.run(['psql',DATABASE_URL,'-v','ON_ERROR_STOP=1'],stdin=src)
raise SystemExit(p.returncode)
