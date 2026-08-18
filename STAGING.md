# Ambiente de staging

Staging é uma cópia funcional do SaaS, mas com banco e integrações separados da produção. Clientes pagantes nunca devem usar esse ambiente.

## Teste local com PostgreSQL
Com Docker instalado:

```bash
docker compose -f docker-compose.staging.yml up --build
```

Abra `http://localhost:8001`. O banco fica em volume separado.

## Staging hospedado
Use:
- domínio próprio, como `staging.seudominio.com.br`;
- PostgreSQL exclusivo;
- `APP_ENV=staging`;
- `ASAAS_SANDBOX=1`;
- bucket/prefixo de storage separado;
- `CREATE_DEMO_ADMIN=0`.

A branch sugerida é `develop`. O workflow `.github/workflows/deploy-staging.yml` usa o secret `STAGING_DEPLOY_HOOK_URL` para disparar o deploy do provedor escolhido.
