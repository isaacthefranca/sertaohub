from fastapi.testclient import TestClient
from pathlib import Path
import re, os, sqlite3
import main

PASS=[]; FAIL=[]
def check(name, cond, detail=''):
    (PASS if cond else FAIL).append((name, detail))
    print(('PASS' if cond else 'FAIL'), name, detail)

def q(sql, params=(), one=False):
    c=main.db(); r=c.execute(sql,params).fetchone() if one else c.execute(sql,params).fetchall(); c.close(); return r

def execsql(sql, params=()):
    c=main.db(); c.execute(sql,params); c.commit(); c.close()

c=TestClient(main.app, base_url='http://localhost')
# public/basic
for path in ['/', '/health','/ready','/login','/register','/forgot-password','/b/barbearia-modelo','/manifest.json','/service-worker.js']:
    r=c.get(path, follow_redirects=False); check('GET '+path, r.status_code==200, str(r.status_code))
r=c.get('/health'); check('security nosniff', r.headers.get('x-content-type-options')=='nosniff')
check('health env', r.json().get('ok') is True)
check('ready db', c.get('/ready').json().get('ok') is True)
check('unknown barbershop 404', c.get('/b/nao-existe').status_code==404)

# auth invalid + rate basic
r=c.post('/login',data={'email':'demo@barbersaas.com','password':'errada'}); check('login wrong rejected',r.status_code==401)
# clear rate failure to avoid contaminating
main._login_attempts.clear()
r=c.post('/login',data={'email':'demo@barbersaas.com','password':'123456'},follow_redirects=False); check('login demo',r.status_code==303 and r.headers.get('location')=='/app' and c.cookies.get('session'))
# app pages
pages=['/app','/app/services','/app/professionals','/app/schedules','/app/customers','/app/appointments','/app/pdv','/app/cash','/app/finance','/app/products','/app/commissions','/app/memberships','/app/reports','/app/team','/app/settings','/app/branding','/app/billing']
for path in pages:
    r=c.get(path,follow_redirects=False); check('owner page '+path,r.status_code==200,str(r.status_code))

# registration security and new tenant
c2=TestClient(main.app,base_url='http://localhost')
r=c2.post('/register',data={'business_name':'QA Shop','name':'QA Owner','email':'qaowner@example.com','password':'1234567'},follow_redirects=False); check('password min 8 register',r.status_code==400)
r=c2.post('/register',data={'business_name':'QA Shop','name':'QA Owner','email':'qaowner@example.com','password':'Senha123!'},follow_redirects=False); check('register new tenant',r.status_code==303 and r.headers.get('location')=='/app')
qa_user=q("SELECT * FROM users WHERE email='qaowner@example.com'",one=True); check('registered user db',qa_user is not None)
qa_tid=qa_user['tenant_id']; sub=q('SELECT * FROM subscriptions WHERE tenant_id=?',(qa_tid,),one=True); check('trial created',sub and sub['status']=='trial')

# CRUD service demo
before=len(q("SELECT * FROM services WHERE tenant_id=(SELECT tenant_id FROM users WHERE email='demo@barbersaas.com')"))
r=c.post('/app/services',data={'name':'QA 120','category':'Outro','custom_category':'Premium QA','price':'50','duration':'120'},follow_redirects=False); check('add service',r.status_code==303)
svc=q("SELECT * FROM services WHERE name='QA 120'",one=True); check('service saved custom cat',svc and svc['duration']==120 and svc['category']=='Premium QA')
r=c.post(f"/app/services/{svc['id']}/edit",data={'name':'QA 120 Edit','category':'Outro','custom_category':'Premium QA','price':'55','duration':'120'},follow_redirects=False); check('edit service',r.status_code==303 and q('SELECT name FROM services WHERE id=?',(svc['id'],),one=True)['name']=='QA 120 Edit')
# tenant isolation: c2 cannot edit demo service
r=c2.post(f"/app/services/{svc['id']}/edit",data={'name':'HACK','category':'Barba','price':'1','duration':'30'},follow_redirects=False); check('tenant isolation service edit',q('SELECT name FROM services WHERE id=?',(svc['id'],),one=True)['name']=='QA 120 Edit')

# professional/customer crud
r=c.post('/app/professionals',data={'name':'QA Barber','phone':'83999999999','specialty':'Barbeiro','commission':'40'},follow_redirects=False); pro=q("SELECT * FROM professionals WHERE name='QA Barber'",one=True); check('add professional',r.status_code==303 and pro is not None)
r=c.post('/app/customers',data={'name':'QA Cliente','phone':'83988887777','email':'qa@cliente.com'},follow_redirects=False); cust=q("SELECT * FROM customers WHERE name='QA Cliente'",one=True); check('add customer',r.status_code==303 and cust is not None)
# schedule special
r=c.post('/app/schedules/special',data={'professional_id':pro['id'],'date':'2026-08-20','starts':'08:00','ends':'09:00','kind':'extra','note':'QA'},follow_redirects=False); sh=q("SELECT * FROM special_hours WHERE professional_id=? AND note='QA'",(pro['id'],),one=True); check('special hours add',r.status_code==303 and sh is not None)

# public availability + booking conflict using demo barber/services
barber=q("SELECT * FROM professionals WHERE name='João'",one=True); barba=q("SELECT * FROM services WHERE name='Barba'",one=True)
r=c.get('/api/b/barbearia-modelo/availability',params={'professional_id':barber['id'],'service_id':barba['id'],'date':'2026-08-20'}); js=r.json(); check('availability API',r.status_code==200 and 'slots' in js, str(js)[:150])
slot='14:00'
r=c.post('/b/barbearia-modelo/book',data={'service_id':barba['id'],'professional_id':barber['id'],'date':'2026-08-20','time':slot,'name':'Public QA','phone':'83977776666','email':'pubqa@example.com'},follow_redirects=False); check('public booking',r.status_code==303 and 'success=' in r.headers.get('location',''),r.headers.get('location',''))
pub=q("SELECT * FROM appointments a JOIN customers c ON c.id=a.customer_id WHERE c.name='Public QA'",one=True); check('public booking db',pub is not None)
# same slot must conflict
r2=c.post('/b/barbearia-modelo/book',data={'service_id':barba['id'],'professional_id':barber['id'],'date':'2026-08-20','time':slot,'name':'Public QA2','phone':'83977775555','email':''},follow_redirects=False); check('public double-book blocked','error=unavailable' in r2.headers.get('location','') or 'error=conflict' in r2.headers.get('location',''),r2.headers.get('location',''))
# ICS/cancel token exists
if pub and pub['public_token']:
    check('ics export',c.get(f"/b/barbearia-modelo/calendar/{pub['public_token']}.ics").status_code==200)

# reset password capture
captured={}
def fake_email(to,subj,html,text): captured['text']=text; return True
main.send_email=fake_email
main._login_attempts.clear()
r=c.post('/forgot-password',data={'email':'demo@barbersaas.com'}); m=re.search(r'token=([^\s]+)',captured.get('text','')); check('forgot password sends token',r.status_code==200 and m is not None)
if m:
    token=m.group(1)
    r=c.post('/reset-password',data={'token':token,'password':'NovaSenha123!','password_confirm':'NovaSenha123!'}); check('reset password',r.status_code==200)
    r=c.post('/reset-password',data={'token':token,'password':'OutraSenha123!','password_confirm':'OutraSenha123!'}); check('reset token one use',r.status_code==400)
    # restore demo password in db for subsequent sessions
    execsql("UPDATE users SET password_hash=? WHERE email='demo@barbersaas.com'",(main.hash_password('123456'),))

# Team user owner only + barber redirect
r=c.post('/app/team',data={'professional_id':pro['id'],'email':'barberqa@example.com','password':'1234567'},follow_redirects=False); check('team password min 8','error=password' in r.headers.get('location',''))
r=c.post('/app/team',data={'professional_id':pro['id'],'email':'barberqa@example.com','password':'Senha123!'},follow_redirects=False); staff=q("SELECT * FROM users WHERE email='barberqa@example.com'",one=True); check('team add',staff is not None and staff['role']=='barber')
cb=TestClient(main.app,base_url='http://localhost'); r=cb.post('/login',data={'email':'barberqa@example.com','password':'Senha123!'},follow_redirects=False); check('barber login redirect',r.status_code==303 and r.headers.get('location')=='/barber'); check('barber portal',cb.get('/barber').status_code==200); r=cb.get('/app',follow_redirects=False); check('barber blocked owner app',r.status_code==303 and r.headers.get('location')=='/barber')

# Clean operational demo data for atomic money flow
demo_tid=q("SELECT tenant_id FROM users WHERE email='demo@barbersaas.com'",one=True)['tenant_id']
conn=main.db();
for table in ['commission_entries','sale_items','cash_transactions','expenses','sales','appointments','cash_registers']:
    if table=='sale_items': conn.execute('DELETE FROM sale_items WHERE sale_id IN (SELECT id FROM sales WHERE tenant_id=?)',(demo_tid,))
    else: conn.execute(f'DELETE FROM {table} WHERE tenant_id=?',(demo_tid,)) if table not in ['sale_items'] else None
conn.commit(); conn.close()
# open cash
r=c.post('/app/cash/open',data={'opening_balance':'0'},follow_redirects=False); reg=q("SELECT * FROM cash_registers WHERE tenant_id=? AND status='open'",(demo_tid,),one=True); check('cash open',r.status_code==303 and reg is not None)
# create customer/pro/service ids existing
cus=q("SELECT * FROM customers WHERE tenant_id=? ORDER BY id LIMIT 1",(demo_tid,),one=True); pro2=q("SELECT * FROM professionals WHERE tenant_id=? AND name='Carlos'",(demo_tid,),one=True); svc2=q("SELECT * FROM services WHERE tenant_id=? AND name='Barba'",(demo_tid,),one=True)
r=c.post('/app/appointments',data={'customer_id':cus['id'],'professional_id':pro2['id'],'service_id':svc2['id'],'date':'2026-08-20','time':'16:00'},follow_redirects=False); ap=q("SELECT * FROM appointments WHERE tenant_id=? ORDER BY id DESC LIMIT 1",(demo_tid,),one=True); check('internal appointment create',r.status_code==303 and ap is not None)
# conflicting internal booking
r=c.post('/app/appointments',data={'customer_id':cus['id'],'professional_id':pro2['id'],'service_id':svc2['id'],'date':'2026-08-20','time':'16:00'},follow_redirects=False); check('internal conflict blocked','error=conflict' in r.headers.get('location',''))
# complete
c.post(f"/app/appointments/{ap['id']}/status",data={'status':'completed'},follow_redirects=False); check('appointment completed',q('SELECT status FROM appointments WHERE id=?',(ap['id'],),one=True)['status']=='completed')
# pdv pending appears page text
r=c.get('/app/pdv'); check('completed pending PDV',cus['name'] in r.text)
# charge
r=c.post(f"/app/pdv/appointment/{ap['id']}/sale",data={'payment_method':'PIX','discount':'0'},follow_redirects=False); check('appointment charge',r.status_code==303 and 'success=appointment' in r.headers.get('location',''))
sale=q('SELECT * FROM sales WHERE appointment_id=?',(ap['id'],),one=True); check('sale linked appointment',sale and abs(float(sale['total'])-25)<0.001)
tx=q("SELECT * FROM cash_transactions WHERE tenant_id=? AND kind='income'",(demo_tid,),one=True); check('cash income generated',tx and abs(float(tx['amount'])-25)<0.001)
comm=q("SELECT * FROM commission_entries WHERE sale_id=?",(sale['id'],),one=True); check('commission generated',comm and abs(float(comm['amount'])-12.5)<0.001 and comm['status']=='pending')
# duplicate charge blocked
r=c.post(f"/app/pdv/appointment/{ap['id']}/sale",data={'payment_method':'PIX','discount':'0'},follow_redirects=False); check('duplicate appointment charge blocked','error=already_paid' in r.headers.get('location','') or len(q('SELECT * FROM sales WHERE appointment_id=?',(ap['id'],)))==1)
# pay commission atomic
r=c.post(f"/app/commissions/{comm['id']}/pay",follow_redirects=False); comm2=q('SELECT * FROM commission_entries WHERE id=?',(comm['id'],),one=True); out=q("SELECT * FROM cash_transactions WHERE tenant_id=? AND kind='expense'",(demo_tid,),one=True); exp=q("SELECT * FROM expenses WHERE tenant_id=? AND category='Comissão'",(demo_tid,),one=True); check('commission pay status',comm2['status']=='paid'); check('commission cash out',out and abs(float(out['amount'])-12.5)<0.001); check('commission finance expense',exp and abs(float(exp['amount'])-12.5)<0.001)
# idempotent pay
before_out=len(q("SELECT * FROM cash_transactions WHERE tenant_id=? AND kind='expense'",(demo_tid,))); c.post(f"/app/commissions/{comm['id']}/pay",follow_redirects=False); after_out=len(q("SELECT * FROM cash_transactions WHERE tenant_id=? AND kind='expense'",(demo_tid,))); check('commission pay idempotent',before_out==after_out)
# Finance/report/dashboard render after flow
for path in ['/app/finance','/app/reports','/app','/app/cash','/app/commissions']:
    rr=c.get(path); check('post-flow page '+path,rr.status_code==200)
rep=c.get('/app/reports').text; check('reports paid sale amount','25,00' in rep or '25.00' in rep)
# cash close
r=c.post('/app/cash/close',data={'closing_balance':'12.50'},follow_redirects=False); check('cash close',q("SELECT status FROM cash_registers WHERE id=?",(reg['id'],),one=True)['status']=='closed')
# commission with closed cash stays pending: create manual pending and try
conn=main.db(); ceid=conn.execute('INSERT INTO commission_entries(tenant_id,professional_id,gross,percent,amount,status,created_at) VALUES(?,?,?,?,?,?,?)',(demo_tid,pro2['id'],10,50,5,'pending',main.now())).lastrowid; conn.commit(); conn.close(); r=c.post(f'/app/commissions/{ceid}/pay',follow_redirects=False); check('commission blocked cash closed','error=cash_closed' in r.headers.get('location','') and q('SELECT status FROM commission_entries WHERE id=?',(ceid,),one=True)['status']=='pending')

# product stock + PDV with closed/open
c.post('/app/cash/open',data={'opening_balance':'0'},follow_redirects=False)
r=c.post('/app/products',data={'name':'Pomada QA','category':'Finalizador','sku':'QA1','cost':'10','price':'30','stock':'2','min_stock':'1'},follow_redirects=False); prod=q("SELECT * FROM products WHERE tenant_id=? AND name='Pomada QA'",(demo_tid,),one=True); check('product create',prod is not None)
r=c.post('/app/pdv/sale',data={'item_type':'product','item_id':prod['id'],'qty':'1','customer_id':'0','professional_id':'0','discount':'0','payment_method':'CREDIT_CARD'},follow_redirects=False); prod2=q('SELECT * FROM products WHERE id=?',(prod['id'],),one=True); check('product sale stock decrement',abs(float(prod2['stock'])-1)<0.001)
r=c.post('/app/pdv/sale',data={'item_type':'product','item_id':prod['id'],'qty':'5','customer_id':'0','professional_id':'0','discount':'0','payment_method':'PIX'},follow_redirects=False); check('insufficient stock blocked','error=insufficient_stock' in r.headers.get('location',''))

# settings/memberships
r=c.post('/app/settings',data={'whatsapp_number':'5583999999999','reminder_hours':'24','google_review_url':'https://example.com','cancellation_policy':'Teste'},follow_redirects=False); check('settings save',r.status_code==303)
r=c.post('/app/memberships/plan',data={'name':'Clube QA','price':'99','visits':'4','description':'4 cortes'},follow_redirects=False); mp=q("SELECT * FROM membership_plans WHERE tenant_id=? AND name='Clube QA'",(demo_tid,),one=True); check('membership plan create',mp is not None)

# billing fallback no API key
plan=q("SELECT * FROM saas_plans WHERE name='Starter'",one=True)
r=c.post('/app/billing/checkout',data={'plan_id':plan['id'],'method':'PIX'},follow_redirects=False); po=q("SELECT * FROM payment_orders WHERE tenant_id=? ORDER BY id DESC LIMIT 1",(demo_tid,),one=True); check('billing fallback safe',po and po['status']=='configuration_required')
# invalid method
r=c.post('/app/billing/checkout',data={'plan_id':plan['id'],'method':'BOLETO'},follow_redirects=False); check('billing invalid method blocked','error=method' in r.headers.get('location',''))

# webhook duplicate logic dev, no token
payload={'id':'qa-event-1','event':'UNKNOWN','payment':{'id':'x'}}
r=c.post('/webhooks/asaas',json=payload); r2=c.post('/webhooks/asaas',json=payload); check('webhook accepted dev',r.status_code==200); check('webhook duplicate idempotent',r2.status_code==200 and r2.json().get('duplicate') is True)

# tenant isolation customer pages contains only own data
check('tenant isolation tenant ids distinct', qa_tid != demo_tid)
# direct attempt delete demo service from qa client should not deactivate it
c2.post(f"/app/services/{svc['id']}/delete",follow_redirects=False); check('tenant isolation service delete',q('SELECT active FROM services WHERE id=?',(svc['id'],),one=True)['active']==1)

# subscription lock
execsql("UPDATE subscriptions SET status='trial',trial_ends_at='2020-01-01T00:00:00' WHERE tenant_id=?",(qa_tid,))
r=c2.get('/app',follow_redirects=False); check('expired trial blocks app',r.status_code==303 and r.headers.get('location','').startswith('/app/billing'))
r=c2.get('/app/billing',follow_redirects=False); check('billing exempt from lock',r.status_code==200)

print('\nSUMMARY',len(PASS),'passed',len(FAIL),'failed')
if FAIL:
    for x in FAIL: print('FAILED:',x)
    raise SystemExit(1)
