from pathlib import Path
from datetime import datetime
from db_compat import connect, USE_POSTGRES

BASE = Path(__file__).resolve().parent
DB = BASE / 'barbersaas.db'
MIGRATIONS = BASE / 'migrations'

def run_migrations():
    if not USE_POSTGRES:
        return []
    conn = connect(DB)
    applied_now=[]
    try:
        conn.execute('CREATE TABLE IF NOT EXISTS schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)')
        conn.commit()
        applied={r['version'] for r in conn.execute('SELECT version FROM schema_migrations').fetchall()}
        for path in sorted(MIGRATIONS.glob('*.sql')):
            version=path.name
            if version in applied:
                continue
            try:
                conn.executescript(path.read_text(encoding='utf-8'))
                conn.execute('INSERT INTO schema_migrations(version,applied_at) VALUES(?,?)',(version,datetime.utcnow().isoformat(timespec='seconds')))
                conn.commit(); applied_now.append(version)
            except Exception:
                conn.rollback(); raise
        return applied_now
    finally:
        conn.close()

if __name__ == '__main__':
    done=run_migrations()
    print('Migrations aplicadas:', ', '.join(done) if done else 'nenhuma pendente')
