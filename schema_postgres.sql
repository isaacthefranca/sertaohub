CREATE TABLE IF NOT EXISTS tenants(
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL, slug TEXT UNIQUE NOT NULL,
  logo_url TEXT, primary_color TEXT DEFAULT '#111827', secondary_color TEXT DEFAULT '#ffffff', accent_color TEXT DEFAULT '#22c55e',
  phone TEXT, instagram TEXT, address TEXT, description TEXT, cover_url TEXT, tagline TEXT,
  timezone TEXT DEFAULT 'America/Sao_Paulo', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users(
  id BIGSERIAL PRIMARY KEY,
  tenant_id BIGINT REFERENCES tenants(id), name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'owner', active INTEGER DEFAULT 1,
  professional_id BIGINT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS professionals(
  id BIGSERIAL PRIMARY KEY, tenant_id BIGINT NOT NULL REFERENCES tenants(id), name TEXT NOT NULL,
  phone TEXT, specialty TEXT, commission DOUBLE PRECISION DEFAULT 50, photo_url TEXT, active INTEGER DEFAULT 1, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS services(
  id BIGSERIAL PRIMARY KEY, tenant_id BIGINT NOT NULL REFERENCES tenants(id), name TEXT NOT NULL, category TEXT,
  price DOUBLE PRECISION NOT NULL, duration INTEGER NOT NULL DEFAULT 30, active INTEGER DEFAULT 1, online INTEGER DEFAULT 1, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS customers(
  id BIGSERIAL PRIMARY KEY, tenant_id BIGINT NOT NULL REFERENCES tenants(id), name TEXT NOT NULL, phone TEXT NOT NULL,
  email TEXT, notes TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS appointments(
  id BIGSERIAL PRIMARY KEY, tenant_id BIGINT NOT NULL REFERENCES tenants(id), customer_id BIGINT NOT NULL REFERENCES customers(id),
  professional_id BIGINT NOT NULL REFERENCES professionals(id), service_id BIGINT NOT NULL REFERENCES services(id),
  starts_at TEXT NOT NULL, ends_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'confirmed', notes TEXT,
  total DOUBLE PRECISION DEFAULT 0, public_token TEXT, created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_prof_start_active ON appointments(tenant_id, professional_id, starts_at)
WHERE status NOT IN ('cancelled_client','cancelled_shop');
CREATE TABLE IF NOT EXISTS audit_logs(id BIGSERIAL PRIMARY KEY, tenant_id BIGINT, user_id BIGINT, action TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS saas_plans(id BIGSERIAL PRIMARY KEY, name TEXT UNIQUE NOT NULL, price DOUBLE PRECISION NOT NULL, max_professionals INTEGER NOT NULL, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS subscriptions(id BIGSERIAL PRIMARY KEY, tenant_id BIGINT UNIQUE NOT NULL REFERENCES tenants(id), plan_id BIGINT NOT NULL REFERENCES saas_plans(id), status TEXT DEFAULT 'trial', trial_ends_at TEXT, current_period_end TEXT, grace_ends_at TEXT, last_payment_at TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS products(id BIGSERIAL PRIMARY KEY, tenant_id BIGINT NOT NULL, name TEXT NOT NULL, category TEXT, sku TEXT, cost DOUBLE PRECISION DEFAULT 0, price DOUBLE PRECISION DEFAULT 0, stock DOUBLE PRECISION DEFAULT 0, min_stock DOUBLE PRECISION DEFAULT 0, active INTEGER DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS inventory_movements(id BIGSERIAL PRIMARY KEY, tenant_id BIGINT NOT NULL, product_id BIGINT NOT NULL, movement_type TEXT NOT NULL, quantity DOUBLE PRECISION NOT NULL, note TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cash_registers(id BIGSERIAL PRIMARY KEY, tenant_id BIGINT NOT NULL, opened_by BIGINT, opened_at TEXT NOT NULL, closed_at TEXT, opening_balance DOUBLE PRECISION DEFAULT 0, closing_balance DOUBLE PRECISION, status TEXT DEFAULT 'open');
CREATE TABLE IF NOT EXISTS cash_transactions(id BIGSERIAL PRIMARY KEY, tenant_id BIGINT NOT NULL, cash_register_id BIGINT, kind TEXT NOT NULL, category TEXT, description TEXT, amount DOUBLE PRECISION NOT NULL, payment_method TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sales(id BIGSERIAL PRIMARY KEY, tenant_id BIGINT NOT NULL, customer_id BIGINT, professional_id BIGINT, total DOUBLE PRECISION NOT NULL, discount DOUBLE PRECISION DEFAULT 0, payment_method TEXT, status TEXT DEFAULT 'paid', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sale_items(id BIGSERIAL PRIMARY KEY, sale_id BIGINT NOT NULL, item_type TEXT NOT NULL, item_id BIGINT, description TEXT, qty DOUBLE PRECISION DEFAULT 1, unit_price DOUBLE PRECISION NOT NULL, total DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS expenses(id BIGSERIAL PRIMARY KEY, tenant_id BIGINT NOT NULL, kind TEXT DEFAULT 'expense', category TEXT, description TEXT NOT NULL, amount DOUBLE PRECISION NOT NULL, due_date TEXT, paid INTEGER DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS commission_entries(id BIGSERIAL PRIMARY KEY, tenant_id BIGINT NOT NULL, professional_id BIGINT NOT NULL, appointment_id BIGINT, sale_id BIGINT, gross DOUBLE PRECISION NOT NULL, percent DOUBLE PRECISION NOT NULL, amount DOUBLE PRECISION NOT NULL, status TEXT DEFAULT 'pending', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS membership_plans(id BIGSERIAL PRIMARY KEY, tenant_id BIGINT NOT NULL, name TEXT NOT NULL, price DOUBLE PRECISION NOT NULL, visits INTEGER DEFAULT 1, description TEXT, active INTEGER DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS customer_memberships(id BIGSERIAL PRIMARY KEY, tenant_id BIGINT NOT NULL, customer_id BIGINT NOT NULL, plan_id BIGINT NOT NULL, status TEXT DEFAULT 'active', starts_at TEXT NOT NULL, ends_at TEXT, uses_left INTEGER DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS business_hours(id BIGSERIAL PRIMARY KEY, tenant_id BIGINT NOT NULL, professional_id BIGINT, weekday INTEGER NOT NULL, opens TEXT DEFAULT '09:00', closes TEXT DEFAULT '19:00', active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS special_hours(id BIGSERIAL PRIMARY KEY, tenant_id BIGINT NOT NULL, professional_id BIGINT NOT NULL, date TEXT NOT NULL, starts TEXT NOT NULL, ends TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'extra', note TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS payment_orders(id BIGSERIAL PRIMARY KEY, tenant_id BIGINT NOT NULL, plan_id BIGINT NOT NULL, gateway TEXT DEFAULT 'asaas', external_id TEXT, method TEXT NOT NULL, amount DOUBLE PRECISION NOT NULL, status TEXT DEFAULT 'pending', checkout_url TEXT, created_at TEXT NOT NULL, paid_at TEXT);
CREATE TABLE IF NOT EXISTS webhook_events(id BIGSERIAL PRIMARY KEY, gateway TEXT NOT NULL, event_id TEXT UNIQUE, event_type TEXT, payload TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tenant_settings(id BIGSERIAL PRIMARY KEY, tenant_id BIGINT UNIQUE NOT NULL, whatsapp_enabled INTEGER DEFAULT 0, whatsapp_number TEXT, reminder_hours INTEGER DEFAULT 24, google_review_url TEXT, cancellation_policy TEXT, updated_at TEXT);
CREATE INDEX IF NOT EXISTS idx_appointments_tenant_starts ON appointments(tenant_id, starts_at);
CREATE INDEX IF NOT EXISTS idx_customers_tenant_phone ON customers(tenant_id, phone);
CREATE INDEX IF NOT EXISTS idx_sales_tenant_created ON sales(tenant_id, created_at);
CREATE INDEX IF NOT EXISTS idx_products_tenant ON products(tenant_id);

ALTER TABLE tenants ADD COLUMN IF NOT EXISTS cover_url TEXT;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS tagline TEXT;
ALTER TABLE professionals ADD COLUMN IF NOT EXISTS photo_url TEXT;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS public_token TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_appointments_public_token ON appointments(public_token) WHERE public_token IS NOT NULL;

ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS current_period_end TEXT;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS grace_ends_at TEXT;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS last_payment_at TEXT;

-- v7.11: recuperação de senha por e-mail
CREATE TABLE IF NOT EXISTS password_reset_tokens(
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES users(id),
  token_hash TEXT UNIQUE NOT NULL,
  expires_at TEXT NOT NULL,
  used_at TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_password_reset_user ON password_reset_tokens(user_id);
