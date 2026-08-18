ALTER TABLE sales ADD COLUMN IF NOT EXISTS appointment_id BIGINT;
CREATE UNIQUE INDEX IF NOT EXISTS ux_sales_appointment ON sales(tenant_id, appointment_id) WHERE appointment_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_sales_appointment ON sales(appointment_id) WHERE appointment_id IS NOT NULL;
