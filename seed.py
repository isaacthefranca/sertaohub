from main import db, hash_password, now
from datetime import datetime, timedelta
c=db()
if not c.execute("SELECT 1 FROM users WHERE email='demo@barbersaas.com'").fetchone():
    t=c.execute("INSERT INTO tenants(name,slug,primary_color,secondary_color,phone,instagram,address,description,created_at) VALUES(?,?,?,?,?,?,?,?,?)",('Barbearia Modelo','barbearia-modelo','#171717','#ffffff','(83) 99999-0000','@barbeariamodelo','Centro','Cortes, barba e estilo com horário marcado.',now())).lastrowid
    u=c.execute("INSERT INTO users(tenant_id,name,email,password_hash,role,created_at) VALUES(?,?,?,?,?,?)",(t,'Administrador Demo','demo@barbersaas.com',hash_password('123456'),'owner',now())).lastrowid
    pid=[]
    for n in ['João','Carlos','Lucas']:
        pro_id=c.execute("INSERT INTO professionals(tenant_id,name,specialty,commission,created_at) VALUES(?,?,?,?,?)",(t,n,'Barbeiro',50,now())).lastrowid
        pid.append(pro_id)
        for wd in range(7): c.execute("INSERT INTO business_hours(tenant_id,professional_id,weekday,opens,closes,active) VALUES(?,?,?,?,?,?)",(t,pro_id,wd,'09:00','19:00',1 if wd < 6 else 0))
    svc=[]
    for n,cat,pr,dur in [('Corte masculino','Cabelo',40,40),('Barba','Barba',25,30),('Corte + Barba','Combo',60,60)]: svc.append(c.execute("INSERT INTO services(tenant_id,name,category,price,duration,created_at) VALUES(?,?,?,?,?,?)",(t,n,cat,pr,dur,now())).lastrowid)
    cid=[]
    for i,n in enumerate(['Pedro Henrique','Guilherme Alves','Rafael Lima']): cid.append(c.execute("INSERT INTO customers(tenant_id,name,phone,created_at) VALUES(?,?,?,?)",(t,n,f'(83) 99999-00{i+1}',now())).lastrowid)
    plan=c.execute("SELECT id FROM saas_plans WHERE name='Pro'").fetchone()['id']; c.execute("INSERT INTO subscriptions(tenant_id,plan_id,status,trial_ends_at,created_at) VALUES(?,?,?,?,?)",(t,plan,'trial',(datetime.now()+timedelta(days=7)).isoformat(timespec='seconds'),now()))
    for i in range(3):
        st=(datetime.now()+timedelta(days=i+1)).replace(hour=9+i,minute=0,second=0,microsecond=0); en=st+timedelta(minutes=[40,30,60][i]); c.execute("INSERT INTO appointments(tenant_id,customer_id,professional_id,service_id,starts_at,ends_at,status,total,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(t,cid[i],pid[i],svc[i],st.isoformat(timespec='seconds'),en.isoformat(timespec='seconds'),'confirmed',[40,25,60][i],now()))
    c.commit(); print('Demo criada: demo@barbersaas.com / 123456')
else: print('Demo já existe')
c.close()
