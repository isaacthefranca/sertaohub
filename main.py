from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pathlib import Path
import hashlib, hmac, secrets, os, json, re, calendar, math, smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from urllib.parse import quote
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / '.env')
from db_compat import connect as db_connect, USE_POSTGRES, integrity_errors
from storage import Storage

BASE = Path(__file__).resolve().parent
DB = BASE / 'barbersaas.db'
UPLOADS = BASE / 'static' / 'uploads'
UPLOADS.mkdir(parents=True, exist_ok=True)
SECRET = os.getenv('APP_SECRET', 'CHANGE-ME-IN-PRODUCTION')
APP_ENV = os.getenv('APP_ENV', 'development').strip().lower()
IS_DEPLOYED = APP_ENV in {'staging','production'}
ALLOWED_HOSTS = [h.strip() for h in os.getenv('ALLOWED_HOSTS','127.0.0.1,localhost').split(',') if h.strip()]
_render_host = os.getenv('RENDER_EXTERNAL_HOSTNAME','').strip()
if _render_host and _render_host not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(_render_host)
APP_BASE_URL = (os.getenv('APP_BASE_URL') or os.getenv('RENDER_EXTERNAL_URL') or 'http://127.0.0.1:8000').rstrip('/')
if IS_DEPLOYED and (SECRET in {'CHANGE-ME-IN-PRODUCTION','dev-only-change-me'} or len(SECRET) < 32):
    raise RuntimeError('APP_SECRET inseguro. Em staging/produção use uma chave aleatória com pelo menos 32 caracteres.')
storage = Storage(UPLOADS)

# Production safety checks
ASAAS_API_KEY = os.getenv('ASAAS_API_KEY','').strip()
ASAAS_SANDBOX = os.getenv('ASAAS_SANDBOX','1').strip() == '1'
ASAAS_WEBHOOK_TOKEN = os.getenv('ASAAS_WEBHOOK_TOKEN','').strip()
if APP_ENV == 'production' and ASAAS_API_KEY and not ASAAS_SANDBOX and not ASAAS_WEBHOOK_TOKEN:
    raise RuntimeError('ASAAS_WEBHOOK_TOKEN é obrigatório em produção quando cobranças reais do Asaas estão habilitadas.')

# ---------------- E-mail (SMTP) ----------------
# Funciona com qualquer provedor que ofereça um endpoint SMTP: Gmail/Workspace,
# Resend (smtp.resend.com), SendGrid (smtp.sendgrid.net), Mailgun, Amazon SES, etc.
SMTP_HOST = os.getenv('SMTP_HOST', '').strip()
SMTP_PORT = int(os.getenv('SMTP_PORT', '587') or '587')
SMTP_USER = os.getenv('SMTP_USER', '').strip()
SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', '').strip()
SMTP_FROM = os.getenv('SMTP_FROM', '').strip() or SMTP_USER
SMTP_USE_TLS = os.getenv('SMTP_USE_TLS', '1').strip() not in {'0', 'false', 'False'}
EMAIL_CONFIGURED = bool(SMTP_HOST and SMTP_USER and SMTP_PASSWORD)

def send_email(to_email:str, subject:str, html_body:str, text_body:str) -> bool:
    """Envia um e-mail via SMTP. Retorna True se enviou (ou simulou em dev), False se falhou."""
    if not EMAIL_CONFIGURED:
        # Em desenvolvimento, loga o link para facilitar testes. Em staging/produção,
        # nunca escreva tokens de redefinição nos logs.
        if IS_DEPLOYED:
            print(f'[E-MAIL NÃO CONFIGURADO] Não foi possível enviar mensagem para {to_email}. Configure SMTP.')
            return False
        print(f'[EMAIL SIMULADO — SMTP não configurado] Para: {to_email} | Assunto: {subject}\n{text_body}')
        return True
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = SMTP_FROM
        msg['To'] = to_email
        msg.attach(MIMEText(text_body, 'plain', 'utf-8'))
        msg.attach(MIMEText(html_body, 'html', 'utf-8'))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            if SMTP_USE_TLS:
                server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, [to_email], msg.as_string())
        return True
    except Exception as e:
        print(f'[ERRO AO ENVIAR E-MAIL] Para: {to_email} | {e}')
        return False

app = FastAPI(title='BarberSaaS', docs_url=None if APP_ENV=='production' else '/docs', redoc_url=None if APP_ENV=='production' else '/redoc')
if ALLOWED_HOSTS:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
app.mount('/static', StaticFiles(directory=BASE/'static'), name='static')
templates = Jinja2Templates(directory=BASE/'templates')

# ---------------- Database ----------------
def db():
    return db_connect(DB)

def now(): return datetime.now().isoformat(timespec='seconds')

def local_now(timezone_name:str='America/Sao_Paulo'):
    try:
        return datetime.now(ZoneInfo(timezone_name or 'America/Sao_Paulo')).replace(tzinfo=None)
    except Exception:
        return datetime.now()

def tenant_now(tenant):
    tz = tenant['timezone'] if tenant and 'timezone' in tenant.keys() and tenant['timezone'] else 'America/Sao_Paulo'
    return local_now(tz)

def init_db():
    conn = db()
    if USE_POSTGRES:
        conn.close()
        from migrate import run_migrations
        run_migrations()
        conn = db()
        for name, price, maxp in [('Starter',49.90,3),('Pro',89.90,10),('Premium',149.90,50)]:
            conn.execute('INSERT INTO saas_plans(name,price,max_professionals) VALUES(?,?,?) ON CONFLICT(name) DO NOTHING',(name,price,maxp))
    else:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS tenants(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL, slug TEXT UNIQUE NOT NULL,
          logo_url TEXT, primary_color TEXT DEFAULT '#111827', secondary_color TEXT DEFAULT '#ffffff',
          phone TEXT, instagram TEXT, address TEXT, description TEXT, cover_url TEXT, tagline TEXT,
          timezone TEXT DEFAULT 'America/Sao_Paulo', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS users(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          tenant_id INTEGER, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
          password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'owner', active INTEGER DEFAULT 1,
          created_at TEXT NOT NULL, FOREIGN KEY(tenant_id) REFERENCES tenants(id)
        );
        CREATE TABLE IF NOT EXISTS professionals(
          id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, name TEXT NOT NULL, phone TEXT, specialty TEXT,
          commission REAL DEFAULT 50, photo_url TEXT, active INTEGER DEFAULT 1, created_at TEXT NOT NULL, FOREIGN KEY(tenant_id) REFERENCES tenants(id)
        );
        CREATE TABLE IF NOT EXISTS services(
          id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, name TEXT NOT NULL, category TEXT,
          price REAL NOT NULL, duration INTEGER NOT NULL DEFAULT 30, active INTEGER DEFAULT 1, online INTEGER DEFAULT 1, created_at TEXT NOT NULL,
          FOREIGN KEY(tenant_id) REFERENCES tenants(id)
        );
        CREATE TABLE IF NOT EXISTS customers(
          id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, name TEXT NOT NULL, phone TEXT NOT NULL, email TEXT, notes TEXT, created_at TEXT NOT NULL,
          FOREIGN KEY(tenant_id) REFERENCES tenants(id)
        );
        CREATE TABLE IF NOT EXISTS appointments(
          id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, customer_id INTEGER NOT NULL, professional_id INTEGER NOT NULL, service_id INTEGER NOT NULL,
          starts_at TEXT NOT NULL, ends_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'confirmed', notes TEXT, total REAL DEFAULT 0, public_token TEXT, created_at TEXT NOT NULL,
          FOREIGN KEY(tenant_id) REFERENCES tenants(id), FOREIGN KEY(customer_id) REFERENCES customers(id),
          FOREIGN KEY(professional_id) REFERENCES professionals(id), FOREIGN KEY(service_id) REFERENCES services(id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ux_prof_start_active ON appointments(tenant_id, professional_id, starts_at) WHERE status NOT IN ('cancelled_client','cancelled_shop');
        CREATE TABLE IF NOT EXISTS audit_logs(id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER, user_id INTEGER, action TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS saas_plans(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, price REAL NOT NULL, max_professionals INTEGER NOT NULL, active INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS subscriptions(id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER UNIQUE NOT NULL, plan_id INTEGER NOT NULL, status TEXT DEFAULT 'trial', trial_ends_at TEXT, created_at TEXT NOT NULL, FOREIGN KEY(tenant_id) REFERENCES tenants(id), FOREIGN KEY(plan_id) REFERENCES saas_plans(id));
        """)
        for name, price, maxp in [('Starter',49.90,3),('Pro',89.90,10),('Premium',149.90,50)]:
            conn.execute('INSERT OR IGNORE INTO saas_plans(name,price,max_professionals) VALUES(?,?,?)',(name,price,maxp))
    conn.commit(); conn.close()

init_db()

def init_extended_db():
    conn=db()
    if not USE_POSTGRES:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, name TEXT NOT NULL, category TEXT, sku TEXT, cost REAL DEFAULT 0, price REAL DEFAULT 0, stock REAL DEFAULT 0, min_stock REAL DEFAULT 0, active INTEGER DEFAULT 1, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS inventory_movements(id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, product_id INTEGER NOT NULL, movement_type TEXT NOT NULL, quantity REAL NOT NULL, note TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS cash_registers(id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, opened_by INTEGER, opened_at TEXT NOT NULL, closed_at TEXT, opening_balance REAL DEFAULT 0, closing_balance REAL, status TEXT DEFAULT 'open');
        CREATE TABLE IF NOT EXISTS cash_transactions(id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, cash_register_id INTEGER, kind TEXT NOT NULL, category TEXT, description TEXT, amount REAL NOT NULL, payment_method TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sales(id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, customer_id INTEGER, professional_id INTEGER, total REAL NOT NULL, discount REAL DEFAULT 0, payment_method TEXT, status TEXT DEFAULT 'paid', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sale_items(id INTEGER PRIMARY KEY AUTOINCREMENT, sale_id INTEGER NOT NULL, item_type TEXT NOT NULL, item_id INTEGER, description TEXT, qty REAL DEFAULT 1, unit_price REAL NOT NULL, total REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS expenses(id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, kind TEXT DEFAULT 'expense', category TEXT, description TEXT NOT NULL, amount REAL NOT NULL, due_date TEXT, paid INTEGER DEFAULT 1, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS commission_entries(id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, professional_id INTEGER NOT NULL, appointment_id INTEGER, sale_id INTEGER, gross REAL NOT NULL, percent REAL NOT NULL, amount REAL NOT NULL, status TEXT DEFAULT 'pending', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS membership_plans(id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, name TEXT NOT NULL, price REAL NOT NULL, visits INTEGER DEFAULT 1, description TEXT, active INTEGER DEFAULT 1, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS customer_memberships(id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, customer_id INTEGER NOT NULL, plan_id INTEGER NOT NULL, status TEXT DEFAULT 'active', starts_at TEXT NOT NULL, ends_at TEXT, uses_left INTEGER DEFAULT 0, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS business_hours(id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, professional_id INTEGER, weekday INTEGER NOT NULL, opens TEXT DEFAULT '09:00', closes TEXT DEFAULT '19:00', active INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS special_hours(id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, professional_id INTEGER NOT NULL, date TEXT NOT NULL, starts TEXT NOT NULL, ends TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'extra', note TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS payment_orders(id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, plan_id INTEGER NOT NULL, gateway TEXT DEFAULT 'asaas', external_id TEXT, method TEXT NOT NULL, amount REAL NOT NULL, status TEXT DEFAULT 'pending', checkout_url TEXT, created_at TEXT NOT NULL, paid_at TEXT);
        CREATE TABLE IF NOT EXISTS webhook_events(id INTEGER PRIMARY KEY AUTOINCREMENT, gateway TEXT NOT NULL, event_id TEXT UNIQUE, event_type TEXT, payload TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tenant_settings(id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER UNIQUE NOT NULL, whatsapp_enabled INTEGER DEFAULT 0, whatsapp_number TEXT, reminder_hours INTEGER DEFAULT 24, google_review_url TEXT, cancellation_policy TEXT, updated_at TEXT);
        CREATE TABLE IF NOT EXISTS password_reset_tokens(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, token_hash TEXT UNIQUE NOT NULL, expires_at TEXT NOT NULL, used_at TEXT, created_at TEXT NOT NULL, FOREIGN KEY(user_id) REFERENCES users(id));
        """)
        cols=[r['name'] for r in conn.execute('PRAGMA table_info(users)').fetchall()]
        if 'professional_id' not in cols: conn.execute('ALTER TABLE users ADD COLUMN professional_id INTEGER')
        tcols=[r['name'] for r in conn.execute('PRAGMA table_info(tenants)').fetchall()]
        if 'accent_color' not in tcols: conn.execute("ALTER TABLE tenants ADD COLUMN accent_color TEXT DEFAULT '#22c55e'")
        if 'cover_url' not in tcols: conn.execute("ALTER TABLE tenants ADD COLUMN cover_url TEXT")
        if 'tagline' not in tcols: conn.execute("ALTER TABLE tenants ADD COLUMN tagline TEXT")
        pcols=[r['name'] for r in conn.execute('PRAGMA table_info(professionals)').fetchall()]
        if 'photo_url' not in pcols: conn.execute("ALTER TABLE professionals ADD COLUMN photo_url TEXT")
        acols=[r['name'] for r in conn.execute('PRAGMA table_info(appointments)').fetchall()]
        if 'public_token' not in acols: conn.execute("ALTER TABLE appointments ADD COLUMN public_token TEXT")
        salecols=[r['name'] for r in conn.execute('PRAGMA table_info(sales)').fetchall()]
        if 'appointment_id' not in salecols: conn.execute("ALTER TABLE sales ADD COLUMN appointment_id INTEGER")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_sales_appointment ON sales(tenant_id, appointment_id) WHERE appointment_id IS NOT NULL")
        subcols=[r['name'] for r in conn.execute('PRAGMA table_info(subscriptions)').fetchall()]
        if 'current_period_end' not in subcols: conn.execute("ALTER TABLE subscriptions ADD COLUMN current_period_end TEXT")
        if 'grace_ends_at' not in subcols: conn.execute("ALTER TABLE subscriptions ADD COLUMN grace_ends_at TEXT")
        if 'last_payment_at' not in subcols: conn.execute("ALTER TABLE subscriptions ADD COLUMN last_payment_at TEXT")
    # Conta demo jamais é criada automaticamente em staging/produção.
    if os.getenv('CREATE_DEMO_ADMIN','1' if APP_ENV=='development' else '0') == '1':
        if not conn.execute("SELECT 1 FROM users WHERE role='superadmin'").fetchone():
            conn.execute('INSERT INTO users(tenant_id,name,email,password_hash,role,created_at) VALUES(NULL,?,?,?,?,?)',('Super Admin','admin@barbersaas.com',hash_password('Admin123!'),'superadmin',now()))
    conn.commit(); conn.close()

# ---------------- Security ----------------
def hash_password(password:str)->str:
    salt=secrets.token_hex(16)
    digest=hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),200_000).hex()
    return f'{salt}${digest}'

def verify_password(password:str, stored:str)->bool:
    try:
      salt,digest=stored.split('$',1)
      candidate=hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),200_000).hex()
      return hmac.compare_digest(candidate,digest)
    except Exception: return False

def hash_token(raw_token:str)->str:
    return hashlib.sha256(raw_token.encode()).hexdigest()

PASSWORD_RESET_TTL_SECONDS = 3600  # 1 hora

def create_password_reset_token(user_id:int) -> str:
    raw_token = secrets.token_urlsafe(32)
    conn=db()
    conn.execute('DELETE FROM password_reset_tokens WHERE user_id=? AND used_at IS NULL', (user_id,))
    expires_at = (datetime.now() + timedelta(seconds=PASSWORD_RESET_TTL_SECONDS)).isoformat(timespec='seconds')
    conn.execute('INSERT INTO password_reset_tokens(user_id,token_hash,expires_at,created_at) VALUES(?,?,?,?)',
                 (user_id, hash_token(raw_token), expires_at, now()))
    conn.commit(); conn.close()
    return raw_token

def consume_password_reset_token(raw_token:str):
    """Retorna o user_id se o token for válido, não expirado e não usado; senão None."""
    conn=db()
    row = conn.execute('SELECT * FROM password_reset_tokens WHERE token_hash=?', (hash_token(raw_token),)).fetchone()
    if not row or row['used_at'] or row['expires_at'] < now():
        conn.close(); return None
    conn.execute('UPDATE password_reset_tokens SET used_at=? WHERE id=?', (now(), row['id']))
    conn.commit(); conn.close()
    return row['user_id']

init_extended_db()

def sign_session(user_id:int)->str:
    payload=f'{user_id}:{int((datetime.now()+timedelta(days=7)).timestamp())}'
    sig=hmac.new(SECRET.encode(),payload.encode(),hashlib.sha256).hexdigest()
    return f'{payload}:{sig}'

def read_session(request:Request):
    raw=request.cookies.get('session')
    if not raw: return None
    try:
      uid,exp,sig=raw.split(':',2)
      payload=f'{uid}:{exp}'
      good=hmac.new(SECRET.encode(),payload.encode(),hashlib.sha256).hexdigest()
      if not hmac.compare_digest(sig,good) or int(exp)<int(datetime.now().timestamp()): return None
      conn=db(); user=conn.execute('SELECT * FROM users WHERE id=? AND active=1',(int(uid),)).fetchone(); conn.close()
      return user
    except Exception: return None

def require_user(request:Request):
    return read_session(request)

def safe_slug(v:str)->str:
    v=v.lower().strip(); v=re.sub(r'[^a-z0-9à-ú -]','',v); v=re.sub(r'[\s_]+','-',v); return v[:60] or secrets.token_hex(4)

EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

# Proteção simples contra força bruta no login (em memória; reseta a cada deploy/restart).
# Em produção com múltiplos workers/instâncias, considere mover para um armazenamento compartilhado (ex.: Redis).
_login_attempts = {}
LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 300

def login_is_locked(key:str) -> bool:
    entry = _login_attempts.get(key)
    if not entry: return False
    count, locked_until = entry
    if locked_until and datetime.now().timestamp() < locked_until: return True
    if locked_until and datetime.now().timestamp() >= locked_until:
        _login_attempts.pop(key, None)
    return False

def login_register_failure(key:str):
    count, locked_until = _login_attempts.get(key, (0, None))
    count += 1
    locked_until = (datetime.now() + timedelta(seconds=LOGIN_LOCKOUT_SECONDS)).timestamp() if count >= LOGIN_MAX_ATTEMPTS else None
    _login_attempts[key] = (count, locked_until)

def login_clear_failures(key:str):
    _login_attempts.pop(key, None)

def tenant_context(request:Request):
    user=require_user(request)
    if not user: return None,None
    conn=db(); tenant=conn.execute('SELECT * FROM tenants WHERE id=?',(user['tenant_id'],)).fetchone(); conn.close()
    return user,tenant

def log_action(user, action):
    conn=db(); conn.execute('INSERT INTO audit_logs(tenant_id,user_id,action,created_at) VALUES(?,?,?,?)',(user['tenant_id'],user['id'],action,now())); conn.commit(); conn.close()

def subscription_state(tenant_id:int):
    """Calcula aviso e bloqueio do SaaS sem depender do frontend.
    Trial: avisa nos últimos 3 dias e bloqueia no vencimento.
    Plano pago: avisa nos últimos 5 dias; há 3 dias de carência após o vencimento.
    """
    conn=db(); row=conn.execute('''SELECT su.*,sp.name plan,sp.price FROM subscriptions su JOIN saas_plans sp ON sp.id=su.plan_id WHERE su.tenant_id=?''',(tenant_id,)).fetchone(); conn.close()
    if not row: return {'locked':False,'level':'','message':'','days_left':None}
    status=(row['status'] or '').lower()
    if status in {'suspended','canceled'}:
        return {'locked':True,'level':'danger','message':'Seu acesso está suspenso. Regularize o plano para continuar usando o painel.','days_left':0}
    now_dt=datetime.now()
    if status=='trial' and row['trial_ends_at']:
        end=datetime.fromisoformat(row['trial_ends_at']); seconds=(end-now_dt).total_seconds(); days=max(0,math.ceil(seconds/86400))
        if seconds<=0: return {'locked':True,'level':'danger','message':'Seu período gratuito terminou. Escolha um plano para continuar usando o BarberSaaS.','days_left':0}
        if days<=3: return {'locked':False,'level':'warning','message':f'Seu teste grátis termina em {days} dia(s). Escolha um plano para não interromper o acesso.','days_left':days}
        return {'locked':False,'level':'','message':'','days_left':days}
    period=row['current_period_end'] if 'current_period_end' in row.keys() else None
    grace=row['grace_ends_at'] if 'grace_ends_at' in row.keys() else None
    if period:
        end=datetime.fromisoformat(period); seconds=(end-now_dt).total_seconds(); days=math.ceil(seconds/86400)
        if seconds>0:
            if days<=5: return {'locked':False,'level':'warning','message':f'Sua mensalidade vence em {max(days,1)} dia(s). Verifique sua cobrança para evitar interrupção.','days_left':days}
            return {'locked':False,'level':'','message':'','days_left':days}
        grace_dt=datetime.fromisoformat(grace) if grace else end+timedelta(days=3)
        if now_dt<=grace_dt:
            left=max(0,math.ceil((grace_dt-now_dt).total_seconds()/86400))
            return {'locked':False,'level':'danger','message':f'Pagamento pendente. Você está no período de carência ({left} dia(s) restante(s)). Regularize para evitar bloqueio.','days_left':0}
        return {'locked':True,'level':'danger','message':'Sua assinatura está vencida e o período de carência terminou. Regularize o pagamento para liberar o painel.','days_left':0}
    if status=='past_due':
        return {'locked':True,'level':'danger','message':'Há um pagamento pendente. Regularize sua assinatura para continuar usando o painel.','days_left':0}
    return {'locked':False,'level':'','message':'','days_left':None}

@app.middleware('http')
async def security_headers_middleware(request:Request, call_next):
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    if APP_ENV == 'production':
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response

@app.middleware('http')
async def subscription_access_middleware(request:Request, call_next):
    path=request.url.path
    if path.startswith('/app'):
        user=read_session(request)
        if user and user['tenant_id']:
            # Barbeiros usam o portal simplificado /barber; o painel /app é só do dono da barbearia.
            if user['role']=='barber':
                return RedirectResponse('/barber',303)
            state=subscription_state(user['tenant_id'])
            request.state.subscription_notice=state
            exempt=path.startswith('/app/billing')
            if state['locked'] and not exempt:
                return RedirectResponse('/app/billing?locked=1',303)
    return await call_next(request)

# ---------------- Public/Auth ----------------
@app.get('/', response_class=HTMLResponse)
def central_landing(request:Request):
    return templates.TemplateResponse('central_landing.html', {'request':request})

@app.get('/barberhub', response_class=HTMLResponse)
def barberhub_landing(request:Request):
    return templates.TemplateResponse('landing.html', {'request':request})

@app.get('/health')
def health():
    return {'ok': True, 'service': 'barbersaas', 'env': APP_ENV}

@app.get('/ready')
def ready():
    try:
        conn=db(); conn.execute('SELECT 1').fetchone(); conn.close()
        return {'ok': True, 'database': 'postgresql' if USE_POSTGRES else 'sqlite'}
    except Exception as exc:
        return JSONResponse({'ok': False, 'database': 'unavailable'}, status_code=503)

@app.get('/register', response_class=HTMLResponse)
def register_page(request:Request):
    return templates.TemplateResponse('register.html', {'request':request,'error':None})

@app.post('/register')
def register(request:Request, business_name:str=Form(...), name:str=Form(...), email:str=Form(...), password:str=Form(...)):
    email=email.strip().lower()
    if not EMAIL_RE.match(email):
      return templates.TemplateResponse('register.html', {'request':request,'error':'Informe um e-mail válido.'}, status_code=400)
    if len(password)<8:
      return templates.TemplateResponse('register.html', {'request':request,'error':'Use uma senha com pelo menos 8 caracteres.'}, status_code=400)
    if not business_name.strip() or not name.strip():
      return templates.TemplateResponse('register.html', {'request':request,'error':'Preencha o nome da barbearia e o seu nome.'}, status_code=400)
    conn=db(); slug=safe_slug(business_name); base=slug; n=1
    while conn.execute('SELECT 1 FROM tenants WHERE slug=?',(slug,)).fetchone(): n+=1; slug=f'{base}-{n}'
    try:
      cur=conn.execute('INSERT INTO tenants(name,slug,created_at) VALUES(?,?,?)',(business_name,slug,now())); tenant_id=cur.lastrowid
      cur=conn.execute('INSERT INTO users(tenant_id,name,email,password_hash,role,created_at) VALUES(?,?,?,?,?,?)',(tenant_id,name,email.lower(),hash_password(password),'owner',now())); uid=cur.lastrowid
      plan=conn.execute("SELECT id FROM saas_plans WHERE name='Starter'").fetchone()
      conn.execute('INSERT INTO subscriptions(tenant_id,plan_id,status,trial_ends_at,created_at) VALUES(?,?,?,?,?)',(tenant_id,plan['id'],'trial',(datetime.now()+timedelta(days=7)).isoformat(timespec='seconds'),now()))
      conn.commit()
    except integrity_errors():
      conn.rollback(); conn.close(); return templates.TemplateResponse('register.html',{'request':request,'error':'Este e-mail já está cadastrado.'},status_code=400)
    conn.close(); resp=RedirectResponse('/app',303); resp.set_cookie('session',sign_session(uid),httponly=True,samesite='lax',secure=IS_DEPLOYED,max_age=604800); return resp

@app.get('/login', response_class=HTMLResponse)
def login_page(request:Request):
    return templates.TemplateResponse('login.html',{'request':request,'error':None})

@app.post('/login')
def login(request:Request,email:str=Form(...),password:str=Form(...)):
    email=email.strip().lower()
    ip=request.client.host if request.client else 'unknown'
    key=f'{email}:{ip}'
    if login_is_locked(key):
        return templates.TemplateResponse('login.html',{'request':request,'error':'Muitas tentativas incorretas. Aguarde alguns minutos e tente novamente.'},status_code=429)
    conn=db(); user=conn.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone(); conn.close()
    if not user or not verify_password(password,user['password_hash']) or not user['active']:
      login_register_failure(key)
      return templates.TemplateResponse('login.html',{'request':request,'error':'E-mail ou senha inválidos.'},status_code=401)
    login_clear_failures(key)
    # Superadmin não tem tenant: mandar para /app quebraria o painel (tenant=None). Vai direto para /admin.
    dest='/admin' if user['role']=='superadmin' else ('/barber' if user['role']=='barber' else '/app')
    resp=RedirectResponse(dest,303); resp.set_cookie('session',sign_session(user['id']),httponly=True,samesite='lax',secure=IS_DEPLOYED,max_age=604800); return resp

@app.get('/logout')
def logout():
    resp=RedirectResponse('/login',303); resp.delete_cookie('session'); return resp

@app.get('/forgot-password', response_class=HTMLResponse)
def forgot_password_page(request:Request):
    return templates.TemplateResponse('forgot_password.html',{'request':request,'sent':False})

@app.post('/forgot-password')
def forgot_password_submit(request:Request, email:str=Form(...)):
    email=email.strip().lower()
    ip=request.client.host if request.client else 'unknown'
    key=f'forgot:{email}:{ip}'
    if login_is_locked(key):
        return templates.TemplateResponse('forgot_password.html',{'request':request,'sent':False,'error':'Muitas solicitações. Aguarde alguns minutos e tente novamente.'},status_code=429)
    login_register_failure(key)
    conn=db(); user=conn.execute('SELECT * FROM users WHERE email=? AND active=1',(email,)).fetchone(); conn.close()
    # Sempre responde com a mesma mensagem, exista ou não o e-mail, para não revelar quais e-mails têm conta (evita enumeração).
    if user:
        raw_token = create_password_reset_token(user['id'])
        reset_url = f"{APP_BASE_URL}/reset-password?token={raw_token}"
        html = f"""<p>Olá, {user['name']}.</p><p>Recebemos um pedido para redefinir a senha da sua conta no BarberSaaS.</p>
        <p><a href="{reset_url}">Clique aqui para criar uma nova senha</a></p>
        <p>Este link expira em 1 hora. Se você não pediu isso, pode ignorar este e-mail com segurança.</p>"""
        text = f"Olá, {user['name']}.\n\nPara redefinir sua senha, acesse: {reset_url}\n\nEste link expira em 1 hora. Se você não pediu isso, ignore este e-mail."
        send_email(user['email'], 'Redefinir sua senha — BarberSaaS', html, text)
    return templates.TemplateResponse('forgot_password.html',{'request':request,'sent':True})

@app.get('/reset-password', response_class=HTMLResponse)
def reset_password_page(request:Request, token:str=''):
    if not token:
        return templates.TemplateResponse('reset_password.html',{'request':request,'token':None,'error':'Link inválido.'})
    conn=db(); row=conn.execute('SELECT * FROM password_reset_tokens WHERE token_hash=?',(hash_token(token),)).fetchone(); conn.close()
    if not row or row['used_at'] or row['expires_at']<now():
        return templates.TemplateResponse('reset_password.html',{'request':request,'token':None,'error':'Este link é inválido ou expirou. Solicite um novo.'})
    return templates.TemplateResponse('reset_password.html',{'request':request,'token':token,'error':None})

@app.post('/reset-password')
def reset_password_submit(request:Request, token:str=Form(...), password:str=Form(...), password_confirm:str=Form(...)):
    if len(password)<8:
        return templates.TemplateResponse('reset_password.html',{'request':request,'token':token,'error':'A senha precisa ter pelo menos 8 caracteres.'},status_code=400)
    if password!=password_confirm:
        return templates.TemplateResponse('reset_password.html',{'request':request,'token':token,'error':'As senhas não conferem.'},status_code=400)
    user_id = consume_password_reset_token(token)
    if not user_id:
        return templates.TemplateResponse('reset_password.html',{'request':request,'token':None,'error':'Este link é inválido ou expirou. Solicite um novo.'},status_code=400)
    conn=db(); conn.execute('UPDATE users SET password_hash=? WHERE id=?',(hash_password(password),user_id)); conn.commit(); conn.close()
    return templates.TemplateResponse('login.html',{'request':request,'error':None,'reset_success':True})

# ---------------- App Dashboard ----------------
@app.get('/app', response_class=HTMLResponse)
def dashboard(request:Request):
    user,tenant=tenant_context(request)
    if not user: return RedirectResponse('/login',303)
    if not tenant: return RedirectResponse('/admin',303)  # conta sem barbearia (ex.: superadmin) não tem painel /app
    conn=db(); tid=tenant['id']; today=tenant_now(tenant).date().isoformat()
    # Receita do dashboard vem de vendas efetivamente pagas, não apenas de atendimentos concluídos.
    revenue=conn.execute("SELECT COALESCE(SUM(total),0) v FROM sales WHERE tenant_id=? AND status='paid' AND substr(created_at,1,10)=?",(tid,today)).fetchone()['v']
    appts=conn.execute("SELECT COUNT(*) c FROM appointments WHERE tenant_id=? AND substr(starts_at,1,10)=?",(tid,today)).fetchone()['c']
    customers=conn.execute('SELECT COUNT(*) c FROM customers WHERE tenant_id=?',(tid,)).fetchone()['c']
    pros=conn.execute('SELECT COUNT(*) c FROM professionals WHERE tenant_id=? AND active=1',(tid,)).fetchone()['c']
    # Próximos atendimentos: somente situações ainda acionáveis. Concluídos, faltas e cancelados não aparecem.
    upcoming=conn.execute('''SELECT a.*,c.name customer,p.name professional,s.name service FROM appointments a
      JOIN customers c ON c.id=a.customer_id JOIN professionals p ON p.id=a.professional_id JOIN services s ON s.id=a.service_id
      WHERE a.tenant_id=? AND a.starts_at>=? AND a.status IN ('confirmed','arrived','in_service')
      ORDER BY a.starts_at LIMIT 8''',(tid,now())).fetchall()
    sub=conn.execute('''SELECT su.*,sp.name plan,sp.price FROM subscriptions su JOIN saas_plans sp ON sp.id=su.plan_id WHERE su.tenant_id=?''',(tid,)).fetchone(); conn.close()
    return templates.TemplateResponse('dashboard.html',{'request':request,'user':user,'tenant':tenant,'stats':{'revenue':revenue,'appts':appts,'customers':customers,'pros':pros},'upcoming':upcoming,'sub':sub})

# ---------------- CRUD helpers/pages ----------------
@app.get('/app/services', response_class=HTMLResponse)
def services_page(request:Request):
    user,tenant=tenant_context(request)
    if not user:return RedirectResponse('/login',303)
    conn=db(); rows=conn.execute('SELECT * FROM services WHERE tenant_id=? AND active=1 ORDER BY name',(tenant['id'],)).fetchall(); conn.close()
    return templates.TemplateResponse('services.html',{'request':request,'user':user,'tenant':tenant,'rows':rows})

@app.post('/app/services')
def add_service(request:Request,name:str=Form(...),category:str=Form('Corte masculino'),custom_category:str=Form(''),price:float=Form(...),duration:int=Form(30)):
    user,tenant=tenant_context(request)
    if not user:return RedirectResponse('/login',303)
    if price<0 or duration<=0: return RedirectResponse('/app/services?error=invalid',303)
    final_category = (custom_category.strip() if category == 'Outro' and custom_category.strip() else category.strip()) or 'Serviço'
    conn=db(); conn.execute('INSERT INTO services(tenant_id,name,category,price,duration,created_at) VALUES(?,?,?,?,?,?)',(tenant['id'],name.strip(),final_category,price,duration,now())); conn.commit(); conn.close(); log_action(user,f'Serviço criado: {name} · {final_category}'); return RedirectResponse('/app/services',303)


@app.post('/app/services/{service_id}/edit')
def edit_service(request:Request,service_id:int,name:str=Form(...),category:str=Form('Corte masculino'),custom_category:str=Form(''),price:float=Form(...),duration:int=Form(30)):
    user,tenant=tenant_context(request)
    if not user:return RedirectResponse('/login',303)
    if price<0 or duration<=0: return RedirectResponse('/app/services?error=invalid',303)
    final_category=(custom_category.strip() if category=='Outro' and custom_category.strip() else category.strip()) or 'Serviço'
    conn=db(); svc=conn.execute('SELECT * FROM services WHERE id=? AND tenant_id=? AND active=1',(service_id,tenant['id'])).fetchone()
    if svc:
        conn.execute('UPDATE services SET name=?,category=?,price=?,duration=? WHERE id=? AND tenant_id=?',(name.strip(),final_category,price,duration,service_id,tenant['id']))
        conn.commit(); log_action(user,f'Serviço editado: {name.strip()}')
    conn.close(); return RedirectResponse('/app/services?edited=1',303)

@app.post('/app/services/{service_id}/delete')
def delete_service(request:Request,service_id:int):
    user,tenant=tenant_context(request)
    if not user:return RedirectResponse('/login',303)
    conn=db(); svc=conn.execute('SELECT * FROM services WHERE id=? AND tenant_id=?',(service_id,tenant['id'])).fetchone()
    if svc:
        # Exclusão lógica preserva histórico financeiro e de atendimentos antigos.
        conn.execute('UPDATE services SET active=0,online=0 WHERE id=? AND tenant_id=?',(service_id,tenant['id'])); conn.commit()
        log=f'Serviço excluído: {svc["name"]}'
    else: log=None
    conn.close()
    if log: log_action(user,log)
    return RedirectResponse('/app/services?deleted=1',303)

@app.get('/app/professionals', response_class=HTMLResponse)
def professionals_page(request:Request):
    user,tenant=tenant_context(request)
    if not user:return RedirectResponse('/login',303)
    conn=db(); rows=conn.execute('SELECT * FROM professionals WHERE tenant_id=? AND active=1 ORDER BY name',(tenant['id'],)).fetchall()
    hours_rows=conn.execute('SELECT * FROM business_hours WHERE tenant_id=? ORDER BY professional_id,weekday',(tenant['id'],)).fetchall()
    hours={}
    for h in hours_rows: hours[(h['professional_id'],h['weekday'])]=h
    conn.close()
    return templates.TemplateResponse('professionals.html',{'request':request,'user':user,'tenant':tenant,'rows':rows,'hours':hours})

@app.post('/app/professionals')
def add_professional(request:Request,name:str=Form(...),phone:str=Form(''),specialty:str=Form('Barbeiro'),commission:float=Form(50)):
    user,tenant=tenant_context(request)
    if not user:return RedirectResponse('/login',303)
    if not name.strip() or commission<0 or commission>100: return RedirectResponse('/app/professionals?error=invalid',303)
    conn=db(); cur=conn.execute('INSERT INTO professionals(tenant_id,name,phone,specialty,commission,created_at) VALUES(?,?,?,?,?,?)',(tenant['id'],name,phone,specialty,commission,now())); pid=cur.lastrowid
    for wd in range(7):
        active=1 if wd < 6 else 0
        conn.execute('INSERT INTO business_hours(tenant_id,professional_id,weekday,opens,closes,active) VALUES(?,?,?,?,?,?)',(tenant['id'],pid,wd,'09:00','19:00',active))
    conn.commit(); conn.close(); log_action(user,f'Profissional criado: {name}'); return RedirectResponse('/app/professionals',303)


@app.post('/app/professionals/{professional_id}/edit')
def edit_professional(request:Request,professional_id:int,name:str=Form(...),phone:str=Form(''),specialty:str=Form('Barbeiro'),commission:float=Form(50)):
    user,tenant=tenant_context(request)
    if not user:return RedirectResponse('/login',303)
    if not name.strip() or commission<0 or commission>100: return RedirectResponse('/app/professionals?error=invalid',303)
    conn=db(); pro=conn.execute('SELECT * FROM professionals WHERE id=? AND tenant_id=? AND active=1',(professional_id,tenant['id'])).fetchone()
    if pro:
        conn.execute('UPDATE professionals SET name=?,phone=?,specialty=?,commission=? WHERE id=? AND tenant_id=?',(name.strip(),phone.strip(),specialty.strip(),commission,professional_id,tenant['id']))
        conn.commit(); log_action(user,f'Profissional editado: {name.strip()}')
    conn.close(); return RedirectResponse('/app/professionals?edited=1',303)

@app.post('/app/professionals/{professional_id}/delete')
def delete_professional(request:Request,professional_id:int):
    user,tenant=tenant_context(request)
    if not user:return RedirectResponse('/login',303)
    conn=db(); pro=conn.execute('SELECT * FROM professionals WHERE id=? AND tenant_id=?',(professional_id,tenant['id'])).fetchone()
    if pro:
        conn.execute('UPDATE professionals SET active=0 WHERE id=? AND tenant_id=?',(professional_id,tenant['id']))
        conn.execute('UPDATE users SET active=0 WHERE tenant_id=? AND professional_id=?',(tenant['id'],professional_id))
        conn.commit(); log=f'Profissional excluído: {pro["name"]}'
    else: log=None
    conn.close()
    if log: log_action(user,log)
    return RedirectResponse('/app/professionals?deleted=1',303)

@app.post('/app/professionals/{professional_id}/hours/{weekday}')
def save_professional_day(request:Request,professional_id:int,weekday:int,opens:str=Form('09:00'),closes:str=Form('19:00'),active:int=Form(0)):
    user,tenant=tenant_context(request)
    if not user:return RedirectResponse('/login',303)
    if weekday not in range(7): return RedirectResponse('/app/professionals',303)
    conn=db(); pro=conn.execute('SELECT 1 FROM professionals WHERE id=? AND tenant_id=?',(professional_id,tenant['id'])).fetchone()
    if not pro: conn.close(); return RedirectResponse('/app/professionals',303)
    existing=conn.execute('SELECT id FROM business_hours WHERE tenant_id=? AND professional_id=? AND weekday=?',(tenant['id'],professional_id,weekday)).fetchone()
    if existing: conn.execute('UPDATE business_hours SET opens=?,closes=?,active=? WHERE id=?',(opens,closes,1 if active else 0,existing['id']))
    else: conn.execute('INSERT INTO business_hours(tenant_id,professional_id,weekday,opens,closes,active) VALUES(?,?,?,?,?,?)',(tenant['id'],professional_id,weekday,opens,closes,1 if active else 0))
    conn.commit(); conn.close(); return RedirectResponse('/app/professionals',303)

@app.get('/app/schedules', response_class=HTMLResponse)
def schedules_page(request:Request):
    user,tenant=tenant_context(request)
    if not user:return RedirectResponse('/login',303)
    conn=db()
    if user['role']=='barber' and user['professional_id']:
        pros=conn.execute('SELECT * FROM professionals WHERE id=? AND tenant_id=?',(user['professional_id'],tenant['id'])).fetchall()
        extras=conn.execute("SELECT sh.*,p.name professional FROM special_hours sh JOIN professionals p ON p.id=sh.professional_id WHERE sh.tenant_id=? AND sh.professional_id=? AND sh.date>=? ORDER BY sh.date,sh.starts",(tenant['id'],user['professional_id'],tenant_now(tenant).date().isoformat())).fetchall()
    else:
        pros=conn.execute('SELECT * FROM professionals WHERE tenant_id=? AND active=1 ORDER BY name',(tenant['id'],)).fetchall()
        extras=conn.execute("SELECT sh.*,p.name professional FROM special_hours sh JOIN professionals p ON p.id=sh.professional_id WHERE sh.tenant_id=? AND sh.date>=? ORDER BY sh.date,sh.starts",(tenant['id'],tenant_now(tenant).date().isoformat())).fetchall()
    hours_rows=conn.execute('SELECT * FROM business_hours WHERE tenant_id=? ORDER BY professional_id,weekday',(tenant['id'],)).fetchall()
    hours={(h['professional_id'],h['weekday']):h for h in hours_rows}
    conn.close()
    return templates.TemplateResponse('schedules.html',{'request':request,'user':user,'tenant':tenant,'pros':pros,'hours':hours,'extras':extras})

@app.post('/app/schedules/weekly/{professional_id}/{weekday}')
def schedules_weekly(request:Request,professional_id:int,weekday:int,opens:str=Form('09:00'),closes:str=Form('19:00'),active:int=Form(0)):
    user,tenant=tenant_context(request)
    if not user:return RedirectResponse('/login',303)
    if weekday not in range(7):return RedirectResponse('/app/schedules',303)
    if user['role']=='barber' and user['professional_id']!=professional_id:return RedirectResponse('/app/schedules',303)
    conn=db(); pro=conn.execute('SELECT id FROM professionals WHERE id=? AND tenant_id=?',(professional_id,tenant['id'])).fetchone()
    if not pro:conn.close();return RedirectResponse('/app/schedules',303)
    row=conn.execute('SELECT id FROM business_hours WHERE tenant_id=? AND professional_id=? AND weekday=?',(tenant['id'],professional_id,weekday)).fetchone()
    if row: conn.execute('UPDATE business_hours SET opens=?,closes=?,active=? WHERE id=?',(opens,closes,1 if active else 0,row['id']))
    else: conn.execute('INSERT INTO business_hours(tenant_id,professional_id,weekday,opens,closes,active) VALUES(?,?,?,?,?,?)',(tenant['id'],professional_id,weekday,opens,closes,1 if active else 0))
    conn.commit();conn.close();return RedirectResponse('/app/schedules',303)

@app.post('/app/schedules/special')
def schedules_special(request:Request,professional_id:int=Form(...),date:str=Form(...),starts:str=Form(...),ends:str=Form(...),kind:str=Form('extra'),note:str=Form('')):
    user,tenant=tenant_context(request)
    if not user:return RedirectResponse('/login',303)
    if user['role']=='barber' and user['professional_id']!=professional_id:return RedirectResponse('/app/schedules',303)
    if kind not in {'extra','blocked'} or not date or not starts or not ends or starts>=ends:return RedirectResponse('/app/schedules?error=invalid',303)
    conn=db();pro=conn.execute('SELECT id FROM professionals WHERE id=? AND tenant_id=?',(professional_id,tenant['id'])).fetchone()
    if not pro:conn.close();return RedirectResponse('/app/schedules',303)
    conn.execute('INSERT INTO special_hours(tenant_id,professional_id,date,starts,ends,kind,note,created_at) VALUES(?,?,?,?,?,?,?,?)',(tenant['id'],professional_id,date,starts,ends,kind,note,now()))
    conn.commit();conn.close();return RedirectResponse('/app/schedules',303)

@app.post('/app/schedules/special/{special_id}/delete')
def schedules_special_delete(request:Request,special_id:int):
    user,tenant=tenant_context(request)
    if not user:return RedirectResponse('/login',303)
    conn=db();row=conn.execute('SELECT * FROM special_hours WHERE id=? AND tenant_id=?',(special_id,tenant['id'])).fetchone()
    if row and (user['role']!='barber' or user['professional_id']==row['professional_id']):
        conn.execute('DELETE FROM special_hours WHERE id=?',(special_id,));conn.commit()
    conn.close();return RedirectResponse('/app/schedules',303)

@app.get('/app/customers', response_class=HTMLResponse)
def customers_page(request:Request):
    user,tenant=tenant_context(request)
    if not user:return RedirectResponse('/login',303)
    conn=db(); rows=conn.execute('''SELECT c.*,COUNT(a.id) visits,COALESCE(SUM(CASE WHEN a.status='completed' THEN a.total ELSE 0 END),0) spent FROM customers c LEFT JOIN appointments a ON a.customer_id=c.id WHERE c.tenant_id=? GROUP BY c.id ORDER BY c.id DESC''',(tenant['id'],)).fetchall(); conn.close()
    return templates.TemplateResponse('customers.html',{'request':request,'user':user,'tenant':tenant,'rows':rows})

@app.post('/app/customers')
def add_customer(request:Request,name:str=Form(...),phone:str=Form(...),email:str=Form('')):
    user,tenant=tenant_context(request)
    if not user:return RedirectResponse('/login',303)
    conn=db(); conn.execute('INSERT INTO customers(tenant_id,name,phone,email,created_at) VALUES(?,?,?,?,?)',(tenant['id'],name,phone,email,now())); conn.commit(); conn.close(); log_action(user,f'Cliente criado: {name}'); return RedirectResponse('/app/customers',303)

@app.get('/app/appointments', response_class=HTMLResponse)
def appointments_page(request:Request):
    user,tenant=tenant_context(request)
    if not user:return RedirectResponse('/login',303)
    tid=tenant['id']; view=request.query_params.get('view','day')
    try: selected=datetime.fromisoformat(request.query_params.get('date') or tenant_now(tenant).date().isoformat()).date()
    except: selected=tenant_now(tenant).date()
    conn=db()
    if view=='week':
        start_date=selected-timedelta(days=selected.weekday()); end_date=start_date+timedelta(days=7)
    elif view=='month':
        start_date=selected.replace(day=1); end_date=(start_date.replace(day=28)+timedelta(days=4)).replace(day=1)
    else:
        start_date=selected; end_date=selected+timedelta(days=1); view='day'
    rows=conn.execute('''SELECT a.*,c.name customer,p.name professional,s.name service FROM appointments a JOIN customers c ON c.id=a.customer_id JOIN professionals p ON p.id=a.professional_id JOIN services s ON s.id=a.service_id WHERE a.tenant_id=? AND a.starts_at>=? AND a.starts_at<? ORDER BY a.starts_at''',(tid,start_date.isoformat()+'T00:00:00',end_date.isoformat()+'T00:00:00')).fetchall()
    pros=conn.execute('SELECT * FROM professionals WHERE tenant_id=? AND active=1 ORDER BY name',(tid,)).fetchall(); svcs=conn.execute('SELECT * FROM services WHERE tenant_id=? AND active=1 ORDER BY name',(tid,)).fetchall(); cust=conn.execute('SELECT * FROM customers WHERE tenant_id=? ORDER BY name',(tid,)).fetchall(); conn.close()
    day_groups={}
    for r in rows: day_groups.setdefault(r['starts_at'][:10],[]).append(r)
    prev_date=selected-timedelta(days=1 if view=='day' else (7 if view=='week' else 30))
    next_date=selected+timedelta(days=1 if view=='day' else (7 if view=='week' else 30))
    week_days=[start_date+timedelta(days=i) for i in range(7)] if view=='week' else []
    month_days=list(calendar.Calendar(firstweekday=0).itermonthdates(selected.year,selected.month)) if view=='month' else []
    return templates.TemplateResponse('appointments.html',{'request':request,'user':user,'tenant':tenant,'rows':rows,'pros':pros,'svcs':svcs,'cust':cust,'selected':selected,'view':view,'prev_date':prev_date,'next_date':next_date,'week_days':week_days,'month_days':month_days,'day_groups':day_groups})

@app.post('/app/appointments')
def add_appointment(request:Request,customer_id:int=Form(...),professional_id:int=Form(...),service_id:int=Form(...),date:str=Form(...),time:str=Form(...)):
    user,tenant=tenant_context(request)
    if not user:return RedirectResponse('/login',303)
    conn=db(); tid=tenant['id']; svc=conn.execute('SELECT * FROM services WHERE id=? AND tenant_id=?',(service_id,tid)).fetchone(); pro=conn.execute('SELECT 1 FROM professionals WHERE id=? AND tenant_id=?',(professional_id,tid)).fetchone(); cus=conn.execute('SELECT 1 FROM customers WHERE id=? AND tenant_id=?',(customer_id,tid)).fetchone()
    if not all([svc,pro,cus]): conn.close(); return RedirectResponse('/app/appointments?error=invalid',303)
    start=datetime.fromisoformat(f'{date}T{time}:00'); end=start+timedelta(minutes=svc['duration'])
    conflict=conn.execute('''SELECT 1 FROM appointments WHERE tenant_id=? AND professional_id=? AND status NOT LIKE 'cancelled%' AND starts_at < ? AND ends_at > ?''',(tid,professional_id,end.isoformat(timespec='seconds'),start.isoformat(timespec='seconds'))).fetchone()
    if conflict: conn.close(); return RedirectResponse('/app/appointments?error=conflict',303)
    conn.execute('INSERT INTO appointments(tenant_id,customer_id,professional_id,service_id,starts_at,ends_at,status,total,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(tid,customer_id,professional_id,service_id,start.isoformat(timespec='seconds'),end.isoformat(timespec='seconds'),'confirmed',svc['price'],now())); conn.commit(); conn.close(); log_action(user,'Agendamento criado'); return RedirectResponse('/app/appointments',303)

@app.post('/app/appointments/{appointment_id}/status')
def set_status(request:Request,appointment_id:int,status:str=Form(...),date:str=Form(''),view:str=Form('day')):
    user,tenant=tenant_context(request)
    if not user:return RedirectResponse('/login',303)
    allowed={'confirmed','arrived','in_service','completed','cancelled_client','cancelled_shop','no_show'}
    if status not in allowed:return RedirectResponse('/app/appointments',303)
    conn=db(); conn.execute('UPDATE appointments SET status=? WHERE id=? AND tenant_id=?',(status,appointment_id,tenant['id'])); conn.commit(); conn.close(); log_action(user,f'Status agendamento #{appointment_id}: {status}')
    if view not in {'day','week','month'}: view='day'
    try: safe_date=datetime.fromisoformat(date).date().isoformat() if date else ''
    except: safe_date=''
    target=f'/app/appointments?view={view}' + (f'&date={safe_date}' if safe_date else '')
    return RedirectResponse(target,303)

@app.post('/app/appointments/{appointment_id}/delete')
def delete_appointment(request:Request,appointment_id:int):
    user,tenant=tenant_context(request)
    if not user:return RedirectResponse('/login',303)
    conn=db(); ap=conn.execute('SELECT id FROM appointments WHERE id=? AND tenant_id=?',(appointment_id,tenant['id'])).fetchone()
    if ap:
        conn.execute('DELETE FROM commission_entries WHERE tenant_id=? AND appointment_id=?',(tenant['id'],appointment_id))
        conn.execute('DELETE FROM appointments WHERE id=? AND tenant_id=?',(appointment_id,tenant['id'])); conn.commit(); deleted=True
    else: deleted=False
    conn.close()
    if deleted: log_action(user,f'Agendamento #{appointment_id} excluído')
    return RedirectResponse('/app/appointments?deleted=1',303)

@app.get('/app/branding', response_class=HTMLResponse)
def branding_page(request:Request):
    user,tenant=tenant_context(request)
    if not user:return RedirectResponse('/login',303)
    return templates.TemplateResponse('branding.html',{'request':request,'user':user,'tenant':tenant})

@app.post('/app/branding')
async def branding_save(request:Request,name:str=Form(...),primary_color:str=Form('#111827'),secondary_color:str=Form('#ffffff'),phone:str=Form(''),instagram:str=Form(''),address:str=Form(''),description:str=Form(''),tagline:str=Form(''),logo:Optional[UploadFile]=File(None),cover:Optional[UploadFile]=File(None)):
    user,tenant=tenant_context(request)
    if not user:return RedirectResponse('/login',303)
    logo_url=tenant['logo_url']
    if logo and logo.filename:
      try:
        data=await logo.read()
        logo_url=storage.save_image(tenant['id'], logo.filename, data)
      except ValueError:
        return RedirectResponse('/app/branding?error=upload',303)
    cover_url=tenant['cover_url'] if 'cover_url' in tenant.keys() else None
    if cover and cover.filename:
        try:
            cover_data=await cover.read()
            cover_url=storage.save_image(tenant['id'], cover.filename, cover_data)
        except ValueError: return RedirectResponse('/app/branding?error=upload',303)
    conn=db(); conn.execute('''UPDATE tenants SET name=?,primary_color=?,secondary_color=?,phone=?,instagram=?,address=?,description=?,tagline=?,logo_url=?,cover_url=? WHERE id=?''',(name,primary_color,secondary_color,phone,instagram,address,description,tagline,logo_url,cover_url,tenant['id'])); conn.commit(); conn.close(); log_action(user,'Identidade visual atualizada'); return RedirectResponse('/app/branding',303)

# ---------------- Public booking ----------------
@app.get('/b/{slug}', response_class=HTMLResponse)
def public_shop(request:Request,slug:str):
    conn=db(); tenant=conn.execute('SELECT * FROM tenants WHERE slug=?',(slug,)).fetchone()
    if not tenant: conn.close(); return HTMLResponse('Barbearia não encontrada',404)
    services=conn.execute('SELECT * FROM services WHERE tenant_id=? AND active=1 AND online=1 ORDER BY category,name',(tenant['id'],)).fetchall(); pros=conn.execute('SELECT * FROM professionals WHERE tenant_id=? AND active=1 ORDER BY name',(tenant['id'],)).fetchall()
    success=request.query_params.get('success'); appointment=None; whatsapp_url=None
    if success:
        appointment=conn.execute('''SELECT a.*,c.name customer,c.phone customer_phone,p.name professional,p.phone professional_phone,s.name service,s.duration,s.price FROM appointments a JOIN customers c ON c.id=a.customer_id JOIN professionals p ON p.id=a.professional_id JOIN services s ON s.id=a.service_id WHERE a.id=? AND a.tenant_id=?''',(success,tenant['id'])).fetchone()
        if appointment:
            target=re.sub(r'\D','',appointment['professional_phone'] or tenant['phone'] or '')
            if target and not target.startswith('55'): target='55'+target
            dt=datetime.fromisoformat(appointment['starts_at'])
            msg=f"Olá, {appointment['professional']}! Acabei de agendar pelo site da {tenant['name']}.\n\nCliente: {appointment['customer']}\nServiço: {appointment['service']}\nData: {dt.strftime('%d/%m/%Y')}\nHorário: {dt.strftime('%H:%M')}\n\nEstou enviando esta confirmação pelo WhatsApp."
            whatsapp_url=f"https://wa.me/{target}?text={quote(msg)}" if target else None
    conn.close()
    return templates.TemplateResponse('public_booking.html',{'request':request,'tenant':tenant,'services':services,'pros':pros,'success':success,'appointment':appointment,'whatsapp_url':whatsapp_url})

@app.get('/api/b/{slug}/availability')
def availability(slug:str,professional_id:int,service_id:int,date:str):
    conn=db(); tenant=conn.execute('SELECT * FROM tenants WHERE slug=?',(slug,)).fetchone()
    if not tenant: conn.close(); return JSONResponse({'error':'not found'},404)
    svc=conn.execute('SELECT * FROM services WHERE id=? AND tenant_id=?',(service_id,tenant['id'])).fetchone(); pro=conn.execute('SELECT * FROM professionals WHERE id=? AND tenant_id=?',(professional_id,tenant['id'])).fetchone()
    if not svc or not pro: conn.close(); return JSONResponse({'error':'invalid'},400)
    try: day=datetime.fromisoformat(date)
    except: conn.close(); return JSONResponse({'error':'invalid_date'},400)
    hours=conn.execute('SELECT * FROM business_hours WHERE tenant_id=? AND professional_id=? AND weekday=?',(tenant['id'],professional_id,day.weekday())).fetchone()
    specials=conn.execute('SELECT * FROM special_hours WHERE tenant_id=? AND professional_id=? AND date=? ORDER BY starts',(tenant['id'],professional_id,date)).fetchall()
    busy=conn.execute("SELECT starts_at,ends_at FROM appointments WHERE tenant_id=? AND professional_id=? AND substr(starts_at,1,10)=? AND status NOT LIKE 'cancelled%'",(tenant['id'],professional_id,date)).fetchall(); conn.close()
    intervals=[]
    if not hours or hours['active']:
        opens=(hours['opens'] if hours else '09:00'); closes=(hours['closes'] if hours else '19:00')
        intervals.append((opens,closes))
    for x in specials:
        if x['kind']=='extra': intervals.append((x['starts'],x['ends']))
    blocks=[(x['starts'],x['ends']) for x in specials if x['kind']=='blocked']
    if not intervals:return {'slots':[],'reason':'closed'}
    busy_intervals=[(datetime.fromisoformat(r['starts_at']),datetime.fromisoformat(r['ends_at'])) for r in busy]
    current=local_now(tenant['timezone'] if tenant and 'timezone' in tenant.keys() and tenant['timezone'] else 'America/Sao_Paulo'); duration=max(5,int(svc['duration'])); slots=[]
    # Os horários de início são oferecidos em uma grade fixa de 30 min.
    # A DURAÇÃO do serviço é usada apenas para validar se TODO o intervalo cabe livre.
    # Ex.: serviço 120 min às 12:00 ocupa [12:00,14:00) e conflita com 13:00-15:00.
    slot_step=30
    for opens,closes in intervals:
        oh,om=map(int,opens.split(':')); ch,cm=map(int,closes.split(':')); cur=day.replace(hour=oh,minute=om,second=0); close=day.replace(hour=ch,minute=cm,second=0)
        while cur+timedelta(minutes=duration)<=close:
            end=cur+timedelta(minutes=duration)
            blocked=False
            for bs,be in blocks:
                bh,bm=map(int,bs.split(':')); eh,em=map(int,be.split(':')); bstart=day.replace(hour=bh,minute=bm,second=0); bend=day.replace(hour=eh,minute=em,second=0)
                if cur < bend and end > bstart: blocked=True; break
            has_conflict=any(cur < busy_end and end > busy_start for busy_start,busy_end in busy_intervals)
            if cur > current and not blocked and not has_conflict:
                slots.append(cur.strftime('%H:%M'))
            cur+=timedelta(minutes=slot_step)
    slots=sorted(set(slots))
    return {'slots':slots,'duration':duration,'step':slot_step}

@app.post('/b/{slug}/book')
def public_book(request:Request,slug:str,service_id:int=Form(...),professional_id:int=Form(...),date:str=Form(...),time:str=Form(...),name:str=Form(...),phone:str=Form(...),email:str=Form('')):
    conn=db(); tenant=conn.execute('SELECT * FROM tenants WHERE slug=?',(slug,)).fetchone()
    if not tenant: conn.close(); return HTMLResponse('Barbearia não encontrada',404)
    tid=tenant['id']; svc=conn.execute('SELECT * FROM services WHERE id=? AND tenant_id=? AND active=1',(service_id,tid)).fetchone(); pro=conn.execute('SELECT * FROM professionals WHERE id=? AND tenant_id=? AND active=1',(professional_id,tid)).fetchone()
    if not svc or not pro: conn.close(); return RedirectResponse(f'/b/{slug}?error=invalid',303)
    start=datetime.fromisoformat(f'{date}T{time}:00'); end=start+timedelta(minutes=svc['duration'])
    if start <= local_now(tenant['timezone'] if tenant and 'timezone' in tenant.keys() and tenant['timezone'] else 'America/Sao_Paulo'): conn.close(); return RedirectResponse(f'/b/{slug}?error=past',303)
    hours=conn.execute('SELECT * FROM business_hours WHERE tenant_id=? AND professional_id=? AND weekday=?',(tid,professional_id,start.weekday())).fetchone()
    specials=conn.execute('SELECT * FROM special_hours WHERE tenant_id=? AND professional_id=? AND date=?',(tid,professional_id,date)).fetchall()
    valid_ranges=[]
    if not hours or hours['active']:
        opens=(hours['opens'] if hours else '09:00'); closes=(hours['closes'] if hours else '19:00'); valid_ranges.append((opens,closes))
    valid_ranges += [(x['starts'],x['ends']) for x in specials if x['kind']=='extra']
    inside=False
    for rs,re_ in valid_ranges:
        oh,om=map(int,rs.split(':')); ch,cm=map(int,re_.split(':')); op=start.replace(hour=oh,minute=om); cl=start.replace(hour=ch,minute=cm)
        if start>=op and end<=cl: inside=True; break
    if not inside: conn.close(); return RedirectResponse(f'/b/{slug}?error=closed',303)
    for x in specials:
        if x['kind']=='blocked':
            bh,bm=map(int,x['starts'].split(':')); eh,em=map(int,x['ends'].split(':')); bs=start.replace(hour=bh,minute=bm); be=start.replace(hour=eh,minute=em)
            if start < be and end > bs: conn.close(); return RedirectResponse(f'/b/{slug}?error=closed',303)
    conflict=conn.execute('''SELECT 1 FROM appointments WHERE tenant_id=? AND professional_id=? AND status NOT LIKE 'cancelled%' AND starts_at < ? AND ends_at > ?''',(tid,professional_id,end.isoformat(timespec='seconds'),start.isoformat(timespec='seconds'))).fetchone()
    if conflict: conn.close(); return RedirectResponse(f'/b/{slug}?error=conflict',303)
    customer=conn.execute('SELECT * FROM customers WHERE tenant_id=? AND phone=?',(tid,phone)).fetchone()
    if customer: cid=customer['id']; conn.execute('UPDATE customers SET name=?,email=? WHERE id=?',(name,email,cid))
    else: cid=conn.execute('INSERT INTO customers(tenant_id,name,phone,email,created_at) VALUES(?,?,?,?,?)',(tid,name,phone,email,now())).lastrowid
    token=secrets.token_urlsafe(24)
    cur=conn.execute('INSERT INTO appointments(tenant_id,customer_id,professional_id,service_id,starts_at,ends_at,status,total,public_token,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(tid,cid,professional_id,service_id,start.isoformat(timespec='seconds'),end.isoformat(timespec='seconds'),'confirmed',svc['price'],token,now())); aid=cur.lastrowid
    conn.commit(); conn.close(); return RedirectResponse(f'/b/{slug}?success={aid}#confirmacao',303)

@app.post('/b/{slug}/cancel/{token}')
def public_cancel(slug:str,token:str):
    conn=db(); tenant=conn.execute('SELECT * FROM tenants WHERE slug=?',(slug,)).fetchone()
    if not tenant: conn.close(); return HTMLResponse('Barbearia não encontrada',404)
    ap=conn.execute('SELECT id FROM appointments WHERE tenant_id=? AND public_token=?',(tenant['id'],token)).fetchone()
    if ap: conn.execute("UPDATE appointments SET status='cancelled_client' WHERE id=?",(ap['id'],)); conn.commit()
    conn.close(); return RedirectResponse(f'/b/{slug}?cancelled=1',303)

@app.get('/b/{slug}/calendar/{token}.ics')
def public_calendar(slug:str,token:str):
    conn=db(); tenant=conn.execute('SELECT * FROM tenants WHERE slug=?',(slug,)).fetchone()
    if not tenant: conn.close(); return HTMLResponse('Não encontrado',404)
    ap=conn.execute('''SELECT a.*,s.name service,p.name professional FROM appointments a JOIN services s ON s.id=a.service_id JOIN professionals p ON p.id=a.professional_id WHERE a.tenant_id=? AND a.public_token=?''',(tenant['id'],token)).fetchone(); conn.close()
    if not ap:return HTMLResponse('Não encontrado',404)
    st=datetime.fromisoformat(ap['starts_at']).strftime('%Y%m%dT%H%M%S'); en=datetime.fromisoformat(ap['ends_at']).strftime('%Y%m%dT%H%M%S')
    content=f"BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nDTSTART:{st}\nDTEND:{en}\nSUMMARY:{ap['service']} - {tenant['name']}\nDESCRIPTION:Atendimento com {ap['professional']}\nLOCATION:{tenant['address'] or ''}\nEND:VEVENT\nEND:VCALENDAR"
    return Response(content=content,media_type='text/calendar',headers={'Content-Disposition':'attachment; filename=agendamento.ics'})

@app.get('/manifest.json')
def manifest(): return FileResponse(BASE/'static'/'manifest.json',media_type='application/manifest+json')
@app.get('/service-worker.js')
def service_worker(): return FileResponse(BASE/'static'/'service-worker.js',media_type='application/javascript')


# ================= EXTENDED SaaS MODULES =================
def is_superadmin(user): return bool(user and user['role']=='superadmin')
def require_tenant_user(request):
    user=read_session(request)
    if not user or is_superadmin(user): return None,None
    conn=db(); tenant=conn.execute('SELECT * FROM tenants WHERE id=?',(user['tenant_id'],)).fetchone(); conn.close(); return user,tenant

def money(v): return f"R$ {float(v or 0):,.2f}".replace(',', 'X').replace('.', ',').replace('X','.')
templates.env.filters['money']=money

@app.get('/app/finance', response_class=HTMLResponse)
def finance_page(request:Request):
    user,tenant=require_tenant_user(request)
    if not user:return RedirectResponse('/login',303)
    conn=db(); tid=tenant['id']
    # Extrato financeiro unificado e rastreavel. Vendas sao a fonte da receita de PDV;
    # movimentacoes manuais do Caixa e lancamentos manuais do Financeiro aparecem separadamente.
    rows=[]
    for r in conn.execute("SELECT id,total,payment_method,created_at,appointment_id FROM sales WHERE tenant_id=? AND status='paid' ORDER BY id DESC",(tid,)).fetchall():
        desc=f"Venda #{r['id']}"
        if r['appointment_id']: desc=f"Atendimento #{r['appointment_id']} · Venda #{r['id']}"
        rows.append({'kind':'income','description':desc,'category':'Venda','amount':float(r['total'] or 0),'created_at':r['created_at'],'payment_method':r['payment_method'] or '','source':'PDV'})
    for r in conn.execute("SELECT * FROM cash_transactions WHERE tenant_id=? AND COALESCE(category,'')<>'Venda' ORDER BY id DESC",(tid,)).fetchall():
        rows.append({'kind':r['kind'],'description':r['description'],'category':r['category'] or 'Caixa','amount':float(r['amount'] or 0),'created_at':r['created_at'],'payment_method':r['payment_method'] or '','source':'Caixa'})
    for r in conn.execute("SELECT * FROM expenses WHERE tenant_id=? AND paid=1 ORDER BY id DESC",(tid,)).fetchall():
        rows.append({'kind':r['kind'],'description':r['description'],'category':r['category'] or 'Geral','amount':float(r['amount'] or 0),'created_at':r['created_at'],'payment_method':'','source':'Financeiro'})
    rows=sorted(rows,key=lambda x:x['created_at'] or '',reverse=True)[:100]
    income=sum(r['amount'] for r in rows if r['kind']=='income')
    expense=sum(r['amount'] for r in rows if r['kind']=='expense')
    conn.close(); return templates.TemplateResponse('finance.html',{'request':request,'user':user,'tenant':tenant,'rows':rows,'income':income,'expense':expense})

@app.post('/app/finance')
def finance_add(request:Request,kind:str=Form('expense'),category:str=Form('Geral'),description:str=Form(...),amount:float=Form(...),due_date:str=Form('')):
    user,tenant=require_tenant_user(request)
    if not user:return RedirectResponse('/login',303)
    if not description.strip() or amount<=0 or kind not in {'income','expense'}: return RedirectResponse('/app/finance?error=invalid',303)
    conn=db(); conn.execute('INSERT INTO expenses(tenant_id,kind,category,description,amount,due_date,paid,created_at) VALUES(?,?,?,?,?,?,1,?)',(tenant['id'],kind,category,description,amount,due_date,now())); conn.commit(); conn.close(); log_action(user,f'Financeiro: {description}'); return RedirectResponse('/app/finance',303)

@app.get('/app/products', response_class=HTMLResponse)
def products_page(request:Request):
    user,tenant=require_tenant_user(request)
    if not user:return RedirectResponse('/login',303)
    conn=db(); rows=conn.execute('SELECT * FROM products WHERE tenant_id=? ORDER BY active DESC,name',(tenant['id'],)).fetchall(); conn.close()
    return templates.TemplateResponse('products.html',{'request':request,'user':user,'tenant':tenant,'rows':rows})

@app.post('/app/products')
def products_add(request:Request,name:str=Form(...),category:str=Form('Produtos'),sku:str=Form(''),cost:float=Form(0),price:float=Form(...),stock:float=Form(0),min_stock:float=Form(0)):
    user,tenant=require_tenant_user(request)
    if not user:return RedirectResponse('/login',303)
    if not name.strip() or price<0 or cost<0 or stock<0 or min_stock<0: return RedirectResponse('/app/products?error=invalid',303)
    conn=db(); cur=conn.execute('INSERT INTO products(tenant_id,name,category,sku,cost,price,stock,min_stock,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(tenant['id'],name,category,sku,cost,price,stock,min_stock,now()));
    if stock: conn.execute('INSERT INTO inventory_movements(tenant_id,product_id,movement_type,quantity,note,created_at) VALUES(?,?,?,?,?,?)',(tenant['id'],cur.lastrowid,'initial',stock,'Estoque inicial',now()))
    conn.commit(); conn.close(); return RedirectResponse('/app/products',303)

@app.post('/app/products/{product_id}/stock')
def product_stock(request:Request,product_id:int,quantity:float=Form(...),movement_type:str=Form('entry'),note:str=Form('')):
    user,tenant=require_tenant_user(request)
    if not user:return RedirectResponse('/login',303)
    if quantity<=0: return RedirectResponse('/app/products?error=invalid',303)
    delta=quantity if movement_type=='entry' else -quantity
    conn=db(); conn.execute('UPDATE products SET stock=stock+? WHERE id=? AND tenant_id=?',(delta,product_id,tenant['id'])); conn.execute('INSERT INTO inventory_movements(tenant_id,product_id,movement_type,quantity,note,created_at) VALUES(?,?,?,?,?,?)',(tenant['id'],product_id,movement_type,quantity,note,now())); conn.commit(); conn.close(); return RedirectResponse('/app/products',303)

@app.get('/app/cash', response_class=HTMLResponse)
def cash_page(request:Request):
    user,tenant=require_tenant_user(request)
    if not user:return RedirectResponse('/login',303)
    conn=db(); reg=conn.execute("SELECT * FROM cash_registers WHERE tenant_id=? AND status='open' ORDER BY id DESC LIMIT 1",(tenant['id'],)).fetchone(); tx=conn.execute('SELECT * FROM cash_transactions WHERE tenant_id=? ORDER BY id DESC LIMIT 50',(tenant['id'],)).fetchall()
    income=expense=0.0
    if reg:
        income=float(conn.execute("SELECT COALESCE(SUM(amount),0)v FROM cash_transactions WHERE tenant_id=? AND cash_register_id=? AND kind='income'",(tenant['id'],reg['id'])).fetchone()['v'])
        expense=float(conn.execute("SELECT COALESCE(SUM(amount),0)v FROM cash_transactions WHERE tenant_id=? AND cash_register_id=? AND kind='expense'",(tenant['id'],reg['id'])).fetchone()['v'])
    expected=(float(reg['opening_balance'])+income-expense) if reg else 0
    conn.close()
    return templates.TemplateResponse('cash.html',{'request':request,'user':user,'tenant':tenant,'reg':reg,'tx':tx,'income':income,'expense':expense,'expected':expected})

@app.post('/app/cash/open')
def cash_open(request:Request,opening_balance:float=Form(0)):
    user,tenant=require_tenant_user(request)
    if not user:return RedirectResponse('/login',303)
    if opening_balance<0: return RedirectResponse('/app/cash?error=invalid',303)
    conn=db(); exists=conn.execute("SELECT 1 FROM cash_registers WHERE tenant_id=? AND status='open'",(tenant['id'],)).fetchone()
    if not exists: conn.execute('INSERT INTO cash_registers(tenant_id,opened_by,opened_at,opening_balance,status) VALUES(?,?,?,?,?)',(tenant['id'],user['id'],now(),opening_balance,'open')); conn.commit()
    conn.close(); return RedirectResponse('/app/cash',303)

@app.post('/app/cash/transaction')
def cash_tx(request:Request,kind:str=Form(...),category:str=Form('Geral'),description:str=Form(...),amount:float=Form(...),payment_method:str=Form('PIX')):
    user,tenant=require_tenant_user(request)
    if not user:return RedirectResponse('/login',303)
    if not description.strip() or amount<=0 or kind not in {'income','expense'}: return RedirectResponse('/app/cash?error=invalid',303)
    conn=db(); reg=conn.execute("SELECT * FROM cash_registers WHERE tenant_id=? AND status='open' ORDER BY id DESC LIMIT 1",(tenant['id'],)).fetchone()
    if reg: conn.execute('INSERT INTO cash_transactions(tenant_id,cash_register_id,kind,category,description,amount,payment_method,created_at) VALUES(?,?,?,?,?,?,?,?)',(tenant['id'],reg['id'],kind,category,description,amount,payment_method,now())); conn.commit()
    conn.close(); return RedirectResponse('/app/cash',303)

@app.post('/app/cash/close')
def cash_close(request:Request,closing_balance:float=Form(...)):
    user,tenant=require_tenant_user(request)
    if not user:return RedirectResponse('/login',303)
    conn=db()
    # Não permite fechar o caixa com comissões pendentes: pagar comissão exige
    # caixa aberto, então deixar fechar com pendências travaria o pagamento
    # delas até a abertura de uma nova sessão.
    pending=conn.execute("SELECT COUNT(*)c FROM commission_entries WHERE tenant_id=? AND status='pending'",(tenant['id'],)).fetchone()['c']
    if pending:
        conn.close(); return RedirectResponse('/app/cash?error=pending_commissions',303)
    conn.execute("UPDATE cash_registers SET closing_balance=?,closed_at=?,status='closed' WHERE tenant_id=? AND status='open'",(closing_balance,now(),tenant['id'])); conn.commit(); conn.close(); return RedirectResponse('/app/cash',303)

@app.get('/app/pdv', response_class=HTMLResponse)
def pdv_page(request:Request):
    user,tenant=require_tenant_user(request)
    if not user:return RedirectResponse('/login',303)
    conn=db(); tid=tenant['id']
    products=conn.execute('SELECT * FROM products WHERE tenant_id=? AND active=1 ORDER BY name',(tid,)).fetchall()
    services=conn.execute('SELECT * FROM services WHERE tenant_id=? AND active=1 ORDER BY name',(tid,)).fetchall()
    pros=conn.execute('SELECT * FROM professionals WHERE tenant_id=? AND active=1 ORDER BY name',(tid,)).fetchall()
    customers=conn.execute('SELECT * FROM customers WHERE tenant_id=? ORDER BY name',(tid,)).fetchall()
    sales=conn.execute('SELECT * FROM sales WHERE tenant_id=? ORDER BY id DESC LIMIT 20',(tid,)).fetchall()
    pending=conn.execute("""SELECT a.id,a.starts_at,a.total,c.name customer,p.name professional,s.name service,s.duration,s.price
        FROM appointments a
        JOIN customers c ON c.id=a.customer_id
        JOIN professionals p ON p.id=a.professional_id
        JOIN services s ON s.id=a.service_id
        LEFT JOIN sales sl ON sl.tenant_id=a.tenant_id AND sl.appointment_id=a.id
        WHERE a.tenant_id=? AND a.status='completed' AND sl.id IS NULL
        ORDER BY a.starts_at DESC""",(tid,)).fetchall()
    reg=conn.execute("SELECT * FROM cash_registers WHERE tenant_id=? AND status='open' ORDER BY id DESC LIMIT 1",(tid,)).fetchone()
    today=tenant_now(tenant).date().isoformat()
    today_total=float(conn.execute("SELECT COALESCE(SUM(total),0)v FROM sales WHERE tenant_id=? AND substr(created_at,1,10)=?",(tid,today)).fetchone()['v'])
    today_count=conn.execute("SELECT COUNT(*)c FROM sales WHERE tenant_id=? AND substr(created_at,1,10)=?",(tid,today)).fetchone()['c']
    conn.close()
    return templates.TemplateResponse('pdv.html',{'request':request,'user':user,'tenant':tenant,'products':products,'services':services,'pros':pros,'customers':customers,'sales':sales,'pending':pending,'cash_open':bool(reg),'today_total':today_total,'today_count':today_count})

@app.post('/app/pdv/appointment/{appointment_id}/sale')
def pdv_appointment_sale(request:Request,appointment_id:int,payment_method:str=Form('PIX'),discount:float=Form(0)):
    user,tenant=require_tenant_user(request)
    if not user:return RedirectResponse('/login',303)
    conn=db(); tid=tenant['id']
    reg=conn.execute("SELECT * FROM cash_registers WHERE tenant_id=? AND status='open' ORDER BY id DESC LIMIT 1",(tid,)).fetchone()
    if not reg:
        conn.close(); return RedirectResponse('/app/pdv?error=cash_closed',303)
    ap=conn.execute("""SELECT a.*,c.name customer,p.name professional,p.commission,s.name service,s.price
        FROM appointments a JOIN customers c ON c.id=a.customer_id JOIN professionals p ON p.id=a.professional_id JOIN services s ON s.id=a.service_id
        WHERE a.id=? AND a.tenant_id=?""",(appointment_id,tid)).fetchone()
    if not ap or ap['status']!='completed':
        conn.close(); return RedirectResponse('/app/pdv?error=not_completed',303)
    if conn.execute('SELECT 1 FROM sales WHERE tenant_id=? AND appointment_id=?',(tid,appointment_id)).fetchone():
        conn.close(); return RedirectResponse('/app/pdv?error=already_paid',303)
    gross=float(ap['total'] or ap['price'] or 0); discount=max(0,float(discount or 0))
    if discount>gross:
        conn.close(); return RedirectResponse('/app/pdv?error=invalid_discount',303)
    total=max(0,gross-discount)
    try:
        cur=conn.execute('INSERT INTO sales(tenant_id,customer_id,professional_id,appointment_id,total,discount,payment_method,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(tid,ap['customer_id'],ap['professional_id'],appointment_id,total,discount,payment_method,'paid',now()))
        sid=cur.lastrowid
        conn.execute('INSERT INTO sale_items(sale_id,item_type,item_id,description,qty,unit_price,total) VALUES(?,?,?,?,?,?,?)',(sid,'service',ap['service_id'],ap['service'],1,gross,total))
        conn.execute('INSERT INTO cash_transactions(tenant_id,cash_register_id,kind,category,description,amount,payment_method,created_at) VALUES(?,?,?,?,?,?,?,?)',(tid,reg['id'],'income','Venda',f'Atendimento #{appointment_id} · Venda #{sid}',total,payment_method,now()))
        percent=float(ap['commission'] or 0)
        if percent>0:
            conn.execute('INSERT INTO commission_entries(tenant_id,professional_id,appointment_id,sale_id,gross,percent,amount,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(tid,ap['professional_id'],appointment_id,sid,total,percent,total*percent/100,'pending',now()))
        conn.commit()
    except Exception:
        conn.rollback(); conn.close(); return RedirectResponse('/app/pdv?error=already_paid',303)
    conn.close(); log_action(user,f'Atendimento #{appointment_id} cobrado na venda #{sid}')
    return RedirectResponse(f'/app/pdv?success=appointment&sale_id={sid}',303)

@app.post('/app/pdv/sale')
def pdv_sale(request:Request,item_type:str=Form(...),item_id:int=Form(...),qty:float=Form(1),customer_id:int=Form(0),professional_id:int=Form(0),discount:float=Form(0),payment_method:str=Form('PIX')):
    user,tenant=require_tenant_user(request)
    if not user:return RedirectResponse('/login',303)
    if qty<=0: return RedirectResponse('/app/pdv?error=invalid',303)
    conn=db(); tid=tenant['id']
    reg=conn.execute("SELECT * FROM cash_registers WHERE tenant_id=? AND status='open' ORDER BY id DESC LIMIT 1",(tid,)).fetchone()
    if not reg:
        conn.close(); return RedirectResponse('/app/pdv?error=cash_closed',303)
    table='products' if item_type=='product' else 'services'; item=conn.execute(f'SELECT * FROM {table} WHERE id=? AND tenant_id=?',(item_id,tid)).fetchone()
    if not item: conn.close(); return RedirectResponse('/app/pdv?error=invalid',303)
    unit=float(item['price']); gross=unit*qty; discount=max(0,float(discount or 0))
    if discount>gross: conn.close(); return RedirectResponse('/app/pdv?error=invalid_discount',303)
    if item_type=='product' and float(item['stock'] or 0)<qty: conn.close(); return RedirectResponse('/app/pdv?error=insufficient_stock',303)
    total=max(0,gross-discount); cur=conn.execute('INSERT INTO sales(tenant_id,customer_id,professional_id,total,discount,payment_method,status,created_at) VALUES(?,?,?,?,?,?,?,?)',(tid,customer_id or None,professional_id or None,total,discount,payment_method,'paid',now())); sid=cur.lastrowid
    conn.execute('INSERT INTO sale_items(sale_id,item_type,item_id,description,qty,unit_price,total) VALUES(?,?,?,?,?,?,?)',(sid,item_type,item_id,item['name'],qty,unit,total))
    if item_type=='product': conn.execute('UPDATE products SET stock=stock-? WHERE id=? AND tenant_id=?',(qty,item_id,tid)); conn.execute('INSERT INTO inventory_movements(tenant_id,product_id,movement_type,quantity,note,created_at) VALUES(?,?,?,?,?,?)',(tid,item_id,'sale',qty,f'Venda #{sid}',now()))
    conn.execute('INSERT INTO cash_transactions(tenant_id,cash_register_id,kind,category,description,amount,payment_method,created_at) VALUES(?,?,?,?,?,?,?,?)',(tid,reg['id'],'income','Venda',f'Venda #{sid}',total,payment_method,now()))
    if professional_id:
        pro=conn.execute('SELECT * FROM professionals WHERE id=? AND tenant_id=?',(professional_id,tid)).fetchone()
        if pro: conn.execute('INSERT INTO commission_entries(tenant_id,professional_id,sale_id,gross,percent,amount,status,created_at) VALUES(?,?,?,?,?,?,?,?)',(tid,professional_id,sid,total,pro['commission'],total*pro['commission']/100,'pending',now()))
    conn.commit(); conn.close(); return RedirectResponse('/app/pdv?success=1',303)

@app.get('/app/commissions', response_class=HTMLResponse)
def commissions_page(request:Request):
    user,tenant=require_tenant_user(request)
    if not user:return RedirectResponse('/login',303)
    conn=db(); rows=conn.execute("SELECT ce.*,p.name professional FROM commission_entries ce JOIN professionals p ON p.id=ce.professional_id WHERE ce.tenant_id=? ORDER BY ce.id DESC",(tenant['id'],)).fetchall(); total=conn.execute("SELECT COALESCE(SUM(amount),0)v FROM commission_entries WHERE tenant_id=? AND status='pending'",(tenant['id'],)).fetchone()['v']; conn.close()
    return templates.TemplateResponse('commissions.html',{'request':request,'user':user,'tenant':tenant,'rows':rows,'total':total})

@app.post('/app/commissions/{entry_id}/pay')
def commission_pay(request:Request,entry_id:int):
    user,tenant=require_tenant_user(request)
    if not user:
        return RedirectResponse('/login',303)
    conn=db()
    try:
        entry=conn.execute("""
            SELECT ce.*, p.name AS professional
            FROM commission_entries ce
            JOIN professionals p ON p.id=ce.professional_id
            WHERE ce.id=? AND ce.tenant_id=?
        """,(entry_id,tenant['id'])).fetchone()
        if not entry:
            conn.close(); return RedirectResponse('/app/commissions?error=not_found',303)
        # Idempotencia: uma comissao ja paga nao gera uma segunda despesa.
        if entry['status']!='pending':
            conn.close(); return RedirectResponse('/app/commissions?error=already_paid',303)
        refs=[]
        if entry['appointment_id']:
            refs.append(f"Atendimento #{entry['appointment_id']}")
        if entry['sale_id']:
            refs.append(f"Venda #{entry['sale_id']}")
        ref_text=(' · '+' · '.join(refs)) if refs else ''
        description=f"Comissão - {entry['professional']}{ref_text}"
        # O pagamento da comissão precisa movimentar Caixa e Financeiro na mesma operação.
        reg=conn.execute(
            "SELECT * FROM cash_registers WHERE tenant_id=? AND status='open' ORDER BY id DESC LIMIT 1",
            (tenant['id'],)
        ).fetchone()
        if not reg:
            conn.close(); return RedirectResponse('/app/commissions?error=cash_closed',303)

        paid_at=now()
        amount=float(entry['amount'] or 0)
        # Financeiro: despesa quitada.
        conn.execute(
            'INSERT INTO expenses(tenant_id,kind,category,description,amount,due_date,paid,created_at) VALUES(?,?,?,?,?,?,1,?)',
            (tenant['id'],'expense','Comissão',description,amount,'',paid_at)
        )
        # Caixa: saída vinculada à sessão aberta.
        conn.execute(
            'INSERT INTO cash_transactions(tenant_id,cash_register_id,kind,category,description,amount,payment_method,created_at) VALUES(?,?,?,?,?,?,?,?)',
            (tenant['id'],reg['id'],'expense','Comissão',description,amount,'Comissão',paid_at)
        )
        # Só depois das duas movimentações a comissão é efetivamente baixada.
        cur=conn.execute(
            "UPDATE commission_entries SET status='paid' WHERE id=? AND tenant_id=? AND status='pending'",
            (entry_id,tenant['id'])
        )
        if getattr(cur,'rowcount',1)==0:
            raise RuntimeError('Comissão já processada por outra operação.')
        conn.commit()
    except Exception:
        conn.rollback(); conn.close(); raise
    conn.close()
    log_action(user,f"Comissão paga: {entry['professional']} - {money(entry['amount'])}")
    return RedirectResponse('/app/commissions?paid=1',303)

@app.get('/app/memberships', response_class=HTMLResponse)
def memberships_page(request:Request):
    user,tenant=require_tenant_user(request)
    if not user:return RedirectResponse('/login',303)
    conn=db(); plans=conn.execute('SELECT * FROM membership_plans WHERE tenant_id=? ORDER BY id DESC',(tenant['id'],)).fetchall(); memberships=conn.execute("SELECT cm.*,c.name customer,mp.name plan FROM customer_memberships cm JOIN customers c ON c.id=cm.customer_id JOIN membership_plans mp ON mp.id=cm.plan_id WHERE cm.tenant_id=? ORDER BY cm.id DESC",(tenant['id'],)).fetchall(); customers=conn.execute('SELECT * FROM customers WHERE tenant_id=? ORDER BY name',(tenant['id'],)).fetchall(); conn.close()
    return templates.TemplateResponse('memberships.html',{'request':request,'user':user,'tenant':tenant,'plans':plans,'memberships':memberships,'customers':customers})

@app.post('/app/memberships/plan')
def membership_plan_add(request:Request,name:str=Form(...),price:float=Form(...),visits:int=Form(1),description:str=Form('')):
    user,tenant=require_tenant_user(request)
    if not user:return RedirectResponse('/login',303)
    conn=db(); conn.execute('INSERT INTO membership_plans(tenant_id,name,price,visits,description,created_at) VALUES(?,?,?,?,?,?)',(tenant['id'],name,price,visits,description,now())); conn.commit(); conn.close(); return RedirectResponse('/app/memberships',303)

@app.post('/app/memberships/subscribe')
def membership_subscribe(request:Request,customer_id:int=Form(...),plan_id:int=Form(...)):
    user,tenant=require_tenant_user(request)
    if not user:return RedirectResponse('/login',303)
    conn=db(); plan=conn.execute('SELECT * FROM membership_plans WHERE id=? AND tenant_id=?',(plan_id,tenant['id'])).fetchone()
    if plan: conn.execute('INSERT INTO customer_memberships(tenant_id,customer_id,plan_id,status,starts_at,ends_at,uses_left,created_at) VALUES(?,?,?,?,?,?,?,?)',(tenant['id'],customer_id,plan_id,'active',now(),(datetime.now()+timedelta(days=30)).isoformat(timespec='seconds'),plan['visits'],now())); conn.commit()
    conn.close(); return RedirectResponse('/app/memberships',303)

@app.get('/app/reports', response_class=HTMLResponse)
def reports_page(request:Request):
    user,tenant=require_tenant_user(request)
    if not user:return RedirectResponse('/login',303)
    conn=db(); tid=tenant['id']
    # Relatórios operacionais são derivados de vendas pagas. Isso evita contar agendamentos cancelados,
    # faltas, concluídos ainda não cobrados ou dados estruturais como se fossem faturamento.
    monthly=conn.execute('''
      SELECT substr(sl.created_at,1,7) AS month,
             COUNT(DISTINCT CASE WHEN sl.appointment_id IS NOT NULL THEN sl.appointment_id END) appointments,
             COALESCE(SUM(sl.total),0) revenue
      FROM sales sl
      WHERE sl.tenant_id=? AND sl.status='paid'
      GROUP BY substr(sl.created_at,1,7)
      ORDER BY month DESC LIMIT 12
    ''',(tid,)).fetchall()
    top_services=conn.execute('''
      SELECT COALESCE(si.description,s.name,'Serviço') name,
             COALESCE(SUM(si.qty),0) c,
             COALESCE(SUM(si.total),0) v
      FROM sale_items si
      JOIN sales sl ON sl.id=si.sale_id AND sl.tenant_id=? AND sl.status='paid'
      LEFT JOIN services s ON s.id=si.item_id AND si.item_type='service'
      WHERE si.item_type='service'
      GROUP BY COALESCE(si.description,s.name,'Serviço')
      ORDER BY v DESC, c DESC LIMIT 10
    ''',(tid,)).fetchall()
    top_pros=conn.execute('''
      SELECT p.name,
             COUNT(DISTINCT CASE WHEN sl.appointment_id IS NOT NULL THEN sl.appointment_id END) c,
             COALESCE(SUM(sl.total),0) v
      FROM sales sl
      JOIN professionals p ON p.id=sl.professional_id
      WHERE sl.tenant_id=? AND sl.status='paid'
      GROUP BY p.id,p.name
      ORDER BY v DESC, c DESC LIMIT 10
    ''',(tid,)).fetchall(); conn.close()
    return templates.TemplateResponse('reports.html',{'request':request,'user':user,'tenant':tenant,'monthly':monthly,'top_services':top_services,'top_pros':top_pros})

@app.get('/app/team', response_class=HTMLResponse)
def team_page(request:Request):
    user,tenant=require_tenant_user(request)
    if not user:return RedirectResponse('/login',303)
    if user['role']!='owner': return RedirectResponse('/app',303)
    conn=db(); tid=tenant['id']
    staff=conn.execute("SELECT u.*,p.name professional_name FROM users u LEFT JOIN professionals p ON p.id=u.professional_id WHERE u.tenant_id=? AND u.role='barber' ORDER BY u.active DESC,u.name",(tid,)).fetchall()
    linked_ids={s['professional_id'] for s in staff if s['active']}
    available_pros=conn.execute('SELECT * FROM professionals WHERE tenant_id=? AND active=1 ORDER BY name',(tid,)).fetchall()
    available_pros=[p for p in available_pros if p['id'] not in linked_ids]
    conn.close()
    return templates.TemplateResponse('team.html',{'request':request,'user':user,'tenant':tenant,'staff':staff,'available_pros':available_pros})

@app.post('/app/team')
def team_add(request:Request,professional_id:int=Form(...),email:str=Form(...),password:str=Form(...)):
    user,tenant=require_tenant_user(request)
    if not user:return RedirectResponse('/login',303)
    if user['role']!='owner': return RedirectResponse('/app',303)
    email=email.strip().lower()
    if not EMAIL_RE.match(email): return RedirectResponse('/app/team?error=email',303)
    if len(password)<8: return RedirectResponse('/app/team?error=password',303)
    conn=db(); tid=tenant['id']
    pro=conn.execute('SELECT * FROM professionals WHERE id=? AND tenant_id=? AND active=1',(professional_id,tid)).fetchone()
    if not pro: conn.close(); return RedirectResponse('/app/team?error=invalid',303)
    already=conn.execute("SELECT 1 FROM users WHERE tenant_id=? AND professional_id=? AND active=1",(tid,professional_id)).fetchone()
    if already: conn.close(); return RedirectResponse('/app/team?error=already_linked',303)
    try:
        conn.execute('INSERT INTO users(tenant_id,name,email,password_hash,role,professional_id,created_at) VALUES(?,?,?,?,?,?,?)',(tid,pro['name'],email,hash_password(password),'barber',professional_id,now()))
        conn.commit()
    except integrity_errors():
        conn.rollback(); conn.close(); return RedirectResponse('/app/team?error=email_taken',303)
    conn.close(); log_action(user,f'Login de equipe criado: {pro["name"]} ({email})'); return RedirectResponse('/app/team?created=1',303)

@app.post('/app/team/{user_id}/toggle')
def team_toggle(request:Request,user_id:int):
    user,tenant=require_tenant_user(request)
    if not user:return RedirectResponse('/login',303)
    if user['role']!='owner': return RedirectResponse('/app',303)
    conn=db(); staff=conn.execute("SELECT * FROM users WHERE id=? AND tenant_id=? AND role='barber'",(user_id,tenant['id'])).fetchone()
    if staff:
        conn.execute('UPDATE users SET active=? WHERE id=?',(0 if staff['active'] else 1,user_id)); conn.commit()
        log_action(user,f'Acesso de equipe {"desativado" if staff["active"] else "reativado"}: {staff["email"]}')
    conn.close(); return RedirectResponse('/app/team',303)

@app.post('/app/team/{user_id}/reset-password')
def team_reset_password(request:Request,user_id:int,password:str=Form(...)):
    user,tenant=require_tenant_user(request)
    if not user:return RedirectResponse('/login',303)
    if user['role']!='owner': return RedirectResponse('/app',303)
    if len(password)<8: return RedirectResponse('/app/team?error=password',303)
    conn=db(); staff=conn.execute("SELECT * FROM users WHERE id=? AND tenant_id=? AND role='barber'",(user_id,tenant['id'])).fetchone()
    if staff:
        conn.execute('UPDATE users SET password_hash=? WHERE id=?',(hash_password(password),user_id)); conn.commit()
        log_action(user,f'Senha redefinida para: {staff["email"]}')
    conn.close(); return RedirectResponse('/app/team?reset=1',303)

@app.get('/app/settings', response_class=HTMLResponse)
def settings_page(request:Request):
    user,tenant=require_tenant_user(request)
    if not user:return RedirectResponse('/login',303)
    conn=db(); st=conn.execute('SELECT * FROM tenant_settings WHERE tenant_id=?',(tenant['id'],)).fetchone(); conn.close(); return templates.TemplateResponse('settings.html',{'request':request,'user':user,'tenant':tenant,'st':st})

@app.post('/app/settings')
def settings_save(request:Request,whatsapp_number:str=Form(''),reminder_hours:int=Form(24),google_review_url:str=Form(''),cancellation_policy:str=Form('')):
    user,tenant=require_tenant_user(request)
    if not user:return RedirectResponse('/login',303)
    conn=db(); conn.execute('INSERT INTO tenant_settings(tenant_id,whatsapp_number,reminder_hours,google_review_url,cancellation_policy,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(tenant_id) DO UPDATE SET whatsapp_number=excluded.whatsapp_number,reminder_hours=excluded.reminder_hours,google_review_url=excluded.google_review_url,cancellation_policy=excluded.cancellation_policy,updated_at=excluded.updated_at',(tenant['id'],whatsapp_number,reminder_hours,google_review_url,cancellation_policy,now())); conn.commit(); conn.close(); return RedirectResponse('/app/settings',303)

# Billing / SaaS subscriptions with Asaas hosted checkout (PIX or credit card)
def asaas_checkout(payload):
    import urllib.request, urllib.error
    key=os.getenv('ASAAS_API_KEY','').strip(); sandbox=os.getenv('ASAAS_SANDBOX','1')=='1'
    if not key: return None, 'ASAAS_API_KEY não configurada'
    base='https://api-sandbox.asaas.com/v3' if sandbox else 'https://api.asaas.com/v3'
    data=json.dumps(payload).encode(); req=urllib.request.Request(base+'/checkouts',data=data,method='POST',headers={'Content-Type':'application/json','access_token':key,'User-Agent':'BarberSaaS/1.0'})
    try:
        with urllib.request.urlopen(req,timeout=20) as r: return json.loads(r.read().decode()),None
    except Exception as e: return None,str(e)

@app.get('/app/billing', response_class=HTMLResponse)
def billing_page(request:Request):
    user,tenant=require_tenant_user(request)
    if not user:return RedirectResponse('/login',303)
    conn=db(); plans=conn.execute('SELECT * FROM saas_plans WHERE active=1 ORDER BY price').fetchall(); sub=conn.execute("SELECT su.*,sp.name plan,sp.price FROM subscriptions su JOIN saas_plans sp ON sp.id=su.plan_id WHERE su.tenant_id=?",(tenant['id'],)).fetchone(); orders=conn.execute('SELECT po.*,sp.name plan FROM payment_orders po JOIN saas_plans sp ON sp.id=po.plan_id WHERE po.tenant_id=? ORDER BY po.id DESC LIMIT 20',(tenant['id'],)).fetchall(); conn.close()
    return templates.TemplateResponse('billing.html',{'request':request,'user':user,'tenant':tenant,'plans':plans,'sub':sub,'orders':orders,'gateway_ready':bool(os.getenv('ASAAS_API_KEY'))})

@app.post('/app/billing/checkout')
def billing_checkout(request:Request,plan_id:int=Form(...),method:str=Form(...)):
    user,tenant=require_tenant_user(request)
    if not user:return RedirectResponse('/login',303)
    if method not in {'PIX','CREDIT_CARD'}: return RedirectResponse('/app/billing?error=method',303)
    conn=db(); plan=conn.execute('SELECT * FROM saas_plans WHERE id=? AND active=1',(plan_id,)).fetchone()
    if not plan: conn.close(); return RedirectResponse('/app/billing?error=plan',303)
    base_url=os.getenv('APP_BASE_URL','').rstrip('/') or str(request.base_url).rstrip('/')
    payload={'billingTypes':[method],'chargeTypes':['RECURRENT'],'minutesToExpire':60,'callback':{'successUrl':base_url+'/app/billing?payment=success','cancelUrl':base_url+'/app/billing?payment=cancelled','expiredUrl':base_url+'/app/billing?payment=expired'},'subscription':{'cycle':'MONTHLY','nextDueDate':datetime.now().date().isoformat()},'items':[{'name':f'Plano {plan["name"]} - BarberSaaS','quantity':1,'value':float(plan['price'])}]}
    result,error=asaas_checkout(payload)
    if result:
        ext=result.get('id'); url=result.get('url') or result.get('checkoutUrl') or result.get('link')
        if ext and not url:
            url=(f'https://sandbox.asaas.com/checkoutSession/show/{ext}' if ASAAS_SANDBOX else f'https://asaas.com/checkoutSession/show/{ext}')
        conn.execute('INSERT INTO payment_orders(tenant_id,plan_id,gateway,external_id,method,amount,status,checkout_url,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(tenant['id'],plan_id,'asaas',ext,method,plan['price'],'pending',url,now())); conn.commit(); conn.close()
        if url:return RedirectResponse(url,303)
        return RedirectResponse('/app/billing?created=1',303)
    # dev fallback creates an explicit pending order, never marks it paid
    conn.execute('INSERT INTO payment_orders(tenant_id,plan_id,gateway,method,amount,status,created_at) VALUES(?,?,?,?,?,?,?)',(tenant['id'],plan_id,'asaas',method,plan['price'],'configuration_required',now())); conn.commit(); conn.close(); return RedirectResponse('/app/billing?error=gateway',303)

@app.post('/webhooks/asaas')
async def asaas_webhook(request:Request):
    token=ASAAS_WEBHOOK_TOKEN
    if token and request.headers.get('asaas-access-token')!=token: return JSONResponse({'ok':False},401)
    payload=await request.json(); eid=str(payload.get('id') or payload.get('eventId') or secrets.token_hex(12)); event=payload.get('event','UNKNOWN')
    conn=db()
    try: conn.execute('INSERT INTO webhook_events(gateway,event_id,event_type,payload,created_at) VALUES(?,?,?,?,?)',('asaas',eid,event,json.dumps(payload),now()))
    except integrity_errors(): conn.close(); return {'ok':True,'duplicate':True}
    obj=payload.get('checkout') or payload.get('payment') or payload.get('subscription') or {}
    ext=obj.get('checkoutId') or obj.get('checkoutSession') or obj.get('id')
    paid_events={'PAYMENT_RECEIVED','PAYMENT_CONFIRMED','CHECKOUT_PAID','CHECKOUT_COMPLETED'}
    if ext and event in paid_events:
        order=conn.execute('SELECT * FROM payment_orders WHERE external_id=? ORDER BY id DESC LIMIT 1',(ext,)).fetchone()
        if order:
            paid_now=datetime.now(); paid_iso=paid_now.isoformat(timespec='seconds')
            sub=conn.execute('SELECT * FROM subscriptions WHERE tenant_id=?',(order['tenant_id'],)).fetchone()
            base=paid_now
            if sub and 'current_period_end' in sub.keys() and sub['current_period_end']:
                try:
                    existing_end=datetime.fromisoformat(sub['current_period_end'])
                    if existing_end>base: base=existing_end
                except Exception: pass
            period_end=(base+timedelta(days=30)).isoformat(timespec='seconds')
            grace_end=(base+timedelta(days=33)).isoformat(timespec='seconds')
            conn.execute("UPDATE payment_orders SET status='paid',paid_at=? WHERE id=?",(paid_iso,order['id']))
            conn.execute("UPDATE subscriptions SET plan_id=?,status='active',trial_ends_at=NULL,current_period_end=?,grace_ends_at=?,last_payment_at=? WHERE tenant_id=?",(order['plan_id'],period_end,grace_end,paid_iso,order['tenant_id']))
    elif ext and event in {'CHECKOUT_CANCELED','CHECKOUT_CANCELLED'}:
        conn.execute("UPDATE payment_orders SET status='cancelled' WHERE external_id=? AND status!='paid'",(ext,))
    elif ext and event=='CHECKOUT_EXPIRED':
        conn.execute("UPDATE payment_orders SET status='expired' WHERE external_id=? AND status!='paid'",(ext,))
    conn.commit(); conn.close(); return {'ok':True}

# Barber simplified portal
@app.get('/barber', response_class=HTMLResponse)
def barber_portal(request:Request):
    user=read_session(request)
    if not user:return RedirectResponse('/login',303)
    if user['role']!='barber' or not user['professional_id']: return RedirectResponse('/app',303)
    conn=db(); tenant=conn.execute('SELECT * FROM tenants WHERE id=?',(user['tenant_id'],)).fetchone(); rows=conn.execute("SELECT a.*,c.name customer,s.name service FROM appointments a JOIN customers c ON c.id=a.customer_id JOIN services s ON s.id=a.service_id WHERE a.tenant_id=? AND a.professional_id=? AND substr(a.starts_at,1,10)=? ORDER BY a.starts_at",(user['tenant_id'],user['professional_id'],tenant_now(tenant).date().isoformat())).fetchall(); pending=conn.execute("SELECT COALESCE(SUM(amount),0)v FROM commission_entries WHERE tenant_id=? AND professional_id=? AND status='pending'",(user['tenant_id'],user['professional_id'])).fetchone()['v']; conn.close(); return templates.TemplateResponse('barber.html',{'request':request,'user':user,'tenant':tenant,'rows':rows,'pending':pending})

# Super Admin
@app.get('/admin', response_class=HTMLResponse)
def admin_dashboard(request:Request):
    user=read_session(request)
    if not is_superadmin(user):return RedirectResponse('/login',303)
    conn=db(); stats={'tenants':conn.execute('SELECT COUNT(*)c FROM tenants').fetchone()['c'],'active':conn.execute("SELECT COUNT(*)c FROM subscriptions WHERE status='active'").fetchone()['c'],'trial':conn.execute("SELECT COUNT(*)c FROM subscriptions WHERE status='trial'").fetchone()['c'],'mrr':conn.execute("SELECT COALESCE(SUM(sp.price),0)v FROM subscriptions su JOIN saas_plans sp ON sp.id=su.plan_id WHERE su.status='active'").fetchone()['v']}; tenants=conn.execute("SELECT t.*,su.status,sp.name plan,sp.price FROM tenants t LEFT JOIN subscriptions su ON su.tenant_id=t.id LEFT JOIN saas_plans sp ON sp.id=su.plan_id ORDER BY t.id DESC").fetchall(); conn.close(); return templates.TemplateResponse('admin.html',{'request':request,'user':user,'stats':stats,'tenants':tenants,'tenant':None})

@app.post('/admin/tenant/{tenant_id}/status')
def admin_tenant_status(request:Request,tenant_id:int,status:str=Form(...)):
    user=read_session(request)
    if not is_superadmin(user):return RedirectResponse('/login',303)
    if status not in {'active','trial','past_due','suspended','canceled'}: return RedirectResponse('/admin',303)
    conn=db(); conn.execute('UPDATE subscriptions SET status=? WHERE tenant_id=?',(status,tenant_id)); conn.commit(); conn.close(); return RedirectResponse('/admin',303)
