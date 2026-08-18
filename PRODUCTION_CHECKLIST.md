# BarberSaaS V7 — Checklist de produção

## Regra principal
Nunca substitua o banco de produção por `barbersaas.db`. Código e dados são independentes. Em produção, use PostgreSQL persistente e rode apenas migrações.

## Ambientes
- **Local**: SQLite, desenvolvimento rápido.
- **Staging**: PostgreSQL separado, Asaas Sandbox, domínio `staging...`. Toda atualização entra aqui primeiro.
- **Produção**: PostgreSQL exclusivo, Asaas produção, storage externo e domínio real.

## Antes do primeiro deploy
1. Crie PostgreSQL de staging e PostgreSQL de produção separados.
2. Gere `APP_SECRET` diferente para cada ambiente, com 64+ caracteres.
3. Configure `ALLOWED_HOSTS`.
4. Configure storage S3/R2 para logos e capas.
5. Configure Asaas Sandbox somente em staging.
6. Execute `python migrate.py` antes de subir a aplicação. O `scripts/start.sh` já faz isso.
7. Verifique `/health` e `/ready`.
8. Faça um backup e teste uma restauração em um banco descartável.

## Processo seguro de atualização
1. Desenvolver localmente.
2. Fazer commit em uma branch.
3. CI valida sintaxe/import.
4. Merge em `develop` publica staging.
5. Testar os fluxos críticos.
6. Tirar backup de produção.
7. Publicar produção com aprovação manual.
8. Conferir `/health`, `/ready`, login, agenda e agendamento público.

## Fluxos críticos para teste
Cadastro/login; isolamento entre barbearias; serviço/profissional; horários; agendamento público; conflito de agenda; WhatsApp; PDV; caixa; estoque; assinatura/bloqueio; webhook de pagamento.

## Segredos
Nunca versionar `.env`, chaves Asaas, senha PostgreSQL ou credenciais S3/R2.
