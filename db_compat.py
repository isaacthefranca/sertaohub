import os
import sqlite3
from pathlib import Path

DATABASE_URL = os.getenv('DATABASE_URL', '').strip()
USE_POSTGRES = DATABASE_URL.startswith('postgresql://') or DATABASE_URL.startswith('postgres://')

try:
    import psycopg
    from psycopg.rows import dict_row
except Exception:
    psycopg = None
    dict_row = None

class CursorProxy:
    def __init__(self, cursor, conn_proxy):
        self.cursor = cursor
        self.conn_proxy = conn_proxy

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()

    @property
    def lastrowid(self):
        if not self.conn_proxy.is_postgres:
            return self.cursor.lastrowid
        # PostgreSQL serial/identity sequence value for the latest INSERT in this session.
        cur = self.conn_proxy._conn.execute('SELECT lastval() AS id')
        row = cur.fetchone()
        return row['id'] if isinstance(row, dict) else row[0]

class DBConnection:
    def __init__(self, conn, is_postgres=False):
        self._conn = conn
        self.is_postgres = is_postgres

    def _sql(self, query: str) -> str:
        if not self.is_postgres:
            return query
        # A aplicação usa placeholders '?' no estilo SQLite. O psycopg espera '%s' e trata
        # qualquer outro '%' na string como início de uma diretiva de formatação — então
        # primeiro escapamos os '%' literais que já existem na query (ex.: LIKE 'cancelled%'),
        # e só depois convertemos os placeholders.
        return query.replace('%', '%%').replace('?', '%s')

    def execute(self, query, params=()):
        cur = self._conn.execute(self._sql(query), params)
        return CursorProxy(cur, self)

    def executescript(self, script: str):
        # DDL in this project does not contain semicolons inside string literals.
        for stmt in script.split(';'):
            stmt = stmt.strip()
            if stmt:
                self.execute(stmt)
        return self

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def connect(sqlite_path: Path):
    if USE_POSTGRES:
        if psycopg is None:
            raise RuntimeError('DATABASE_URL aponta para PostgreSQL, mas psycopg não está instalado.')
        url = DATABASE_URL
        if url.startswith('postgres://'):
            url = 'postgresql://' + url[len('postgres://'):]
        conn = psycopg.connect(url, row_factory=dict_row)
        return DBConnection(conn, True)
    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return DBConnection(conn, False)


def integrity_errors():
    errors = [sqlite3.IntegrityError]
    if psycopg is not None:
        errors.append(psycopg.IntegrityError)
    return tuple(errors)
