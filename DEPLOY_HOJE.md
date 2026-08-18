# Deploy hoje — ordem recomendada

## 1. Subir primeiro em staging
Use o `render.yaml` e deixe `ASAAS_SANDBOX=1`.

No primeiro Blueprint, informe os secrets marcados como `sync: false` no painel do Render.

## 2. Configurar no Render
- `ASAAS_API_KEY`: chave do Sandbox Asaas.
- `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`: SMTP real.
- O `APP_SECRET` e o `ASAAS_WEBHOOK_TOKEN` são gerados automaticamente pelo Blueprint.
- O banco PostgreSQL é conectado automaticamente via `DATABASE_URL`.

## 3. Configurar webhook no Asaas Sandbox
URL: `https://SEU-HOST/webhooks/asaas`
Use exatamente o mesmo token de `ASAAS_WEBHOOK_TOKEN` no campo authToken do webhook.
Eventos mínimos para checkout: CHECKOUT_CREATED, CHECKOUT_PAID, CHECKOUT_CANCELED, CHECKOUT_EXPIRED.

## 4. Homologação obrigatória no staging
1. Criar conta nova.
2. Recuperar senha por e-mail real.
3. Criar barbearia, serviço e profissional.
4. Agendar pela página pública em outro celular/navegador.
5. Confirmar que conflito de horários é bloqueado.
6. Abrir caixa, concluir atendimento e cobrar no PDV.
7. Pagar comissão e conferir Caixa/Financeiro.
8. Fechar caixa e conferir Dashboard/Relatórios.
9. Criar checkout Starter/Pro/Premium por PIX no Asaas Sandbox.
10. Confirmar webhook CHECKOUT_PAID e desbloqueio/ativação do plano.
11. Repetir com cartão no Sandbox.
12. Testar checkout cancelado/expirado.

## 5. Produção real
Somente depois dos 12 testes acima:
- Trocar `ASAAS_SANDBOX` para `0`.
- Trocar `ASAAS_API_KEY` pela chave de produção.
- Manter `ASAAS_WEBHOOK_TOKEN` configurado.
- Configurar o webhook equivalente na conta de produção.
- Adicionar o domínio próprio no Render e atualizar DNS.

## 6. Armazenamento de imagens
Para logos/capas permanentes em produção, configure um S3/R2 compatível (`S3_*`). Sem isso, uploads locais podem não ser persistentes entre deploys.
