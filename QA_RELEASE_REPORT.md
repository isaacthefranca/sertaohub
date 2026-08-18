# QA Release Report — BarberSaaS V7.12

Data: 18/08/2026

## Resultado

**91/91 testes automatizados locais aprovados.** Nenhuma falha funcional encontrada na suíte executada contra SQLite.

### Cobertura executada

- Landing, login, cadastro, logout, health/readiness e páginas protegidas.
- Senha mínima, login inválido, owner, barbeiro e isolamento de painel por papel.
- Multiempresa: tentativa de editar/excluir dados de outro tenant bloqueada.
- Serviços, categorias personalizadas, profissionais, clientes e horários especiais.
- Agenda interna, agendamento público, disponibilidade e bloqueio de conflito/dupla reserva.
- Exportação de calendário ICS.
- Recuperação de senha, token de uso único e expiração lógica.
- Caixa aberto/fechado, venda vinculada a atendimento, proteção contra cobrança duplicada.
- Comissão automática, pagamento atômico, saída no caixa, despesa financeira e idempotência.
- Produtos, estoque e bloqueio por estoque insuficiente.
- Financeiro, relatórios e dashboard após operação.
- Clube/planos da barbearia e configurações.
- Billing sem gateway configurado não marca pagamento como pago.
- Webhook idempotente e lifecycle básico de checkout.
- Bloqueio de assinatura/trial vencido.
- Segurança de produção: APP_SECRET fraco bloqueia boot; HSTS ativo; /docs removido; Asaas live exige webhook token.

## Testes adicionais de integração Asaas simulada

- Resposta Asaas contendo apenas `id`: URL de checkout construída corretamente.
- CHECKOUT_PAID: pedido local passa para `paid` e assinatura para `active`.
- CHECKOUT_CANCELED: pedido pendente passa para `cancelled`.
- Evento repetido: idempotência por event_id.

## Limite desta validação

Neste ambiente não há um servidor PostgreSQL externo nem credenciais reais de Asaas/SMTP. Portanto, a release está **aprovada para staging**, mas a liberação de cobrança real deve acontecer somente após a homologação externa descrita em `DEPLOY_HOJE.md`.
