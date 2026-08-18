
CREATE INDEX IF NOT EXISTS idx_professionals_tenant_active ON professionals(tenant_id, active);
CREATE INDEX IF NOT EXISTS idx_services_tenant_active ON services(tenant_id, active);
CREATE INDEX IF NOT EXISTS idx_appointments_prof_starts ON appointments(tenant_id, professional_id, starts_at);
CREATE INDEX IF NOT EXISTS idx_appointments_customer ON appointments(tenant_id, customer_id);
CREATE INDEX IF NOT EXISTS idx_cash_transactions_tenant_created ON cash_transactions(tenant_id, created_at);
CREATE INDEX IF NOT EXISTS idx_expenses_tenant_created ON expenses(tenant_id, created_at);
CREATE INDEX IF NOT EXISTS idx_payment_orders_tenant_created ON payment_orders(tenant_id, created_at);
CREATE INDEX IF NOT EXISTS idx_audit_logs_tenant_created ON audit_logs(tenant_id, created_at);
