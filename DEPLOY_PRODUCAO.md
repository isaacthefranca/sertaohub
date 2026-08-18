# Deploy em produção — BarberSaaS

## Arquitetura recomendada
- Aplicação FastAPI: Render, Railway ou Fly.io via Docker.
- Banco: PostgreSQL gerenciado (Render PostgreSQL, Neon, Supabase ou Railway).
- Logos/arquivos: Cloudflare R2 (API S3 compatível).
- Cobrança: Asaas (PIX e cartão de crédito) usando checkout hospedado + webhook.

## 1. Banco PostgreSQL
Crie um banco PostgreSQL e copie a connection string para `DATABASE_URL`.
Na primeira inicialização, `schema_postgres.sql` é aplicado de forma idempotente e os planos padrão são criados.

## 2. Storage Cloudflare R2
Crie um bucket e uma credencial S3. Configure:
`S3_ENDPOINT_URL`, `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_PUBLIC_URL`.
Sem essas variáveis, uploads continuam locais, adequado apenas para desenvolvimento.

## 3. Asaas
Configure `ASAAS_API_KEY`, `ASAAS_WEBHOOK_TOKEN` e mantenha `ASAAS_SANDBOX=1` até validar cobranças.
Cadastre o webhook apontando para `https://SEU-DOMINIO/webhooks/asaas`.
Depois dos testes, use a chave de produção e `ASAAS_SANDBOX=0`.

## 4. Segurança
Troque `APP_SECRET` por uma chave longa e aleatória. Use HTTPS. Não versionar `.env`.
Troque/remova as credenciais de demonstração antes da operação comercial.

## 5. Docker local com PostgreSQL
Execute `docker compose up --build` e acesse `http://localhost:8000`.

## 6. Migração de dados SQLite existentes
O schema PostgreSQL já está pronto. Para produção nova, comece com banco limpo.
Se precisar transportar clientes existentes do SQLite, faça uma exportação controlada para PostgreSQL antes do corte; não copie o arquivo `.db` para o servidor.
