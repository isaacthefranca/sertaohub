# BarberSaaS — versão ampliada

SaaS multiempresa para barbearias com agenda, clientes, profissionais, serviços, PDV, caixa, estoque, financeiro, comissões, clube, relatórios, white-label, página pública de agendamento, PWA, painel Super Admin e cobrança SaaS por PIX/cartão via Asaas Checkout.

## Rodar localmente
```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
pip install -r requirements.txt
python seed.py
uvicorn main:app --reload
```
Acesse http://127.0.0.1:8000

## Contas de demonstração
- Barbearia: demo@barbersaas.com / 123456
- Super Admin: admin@barbersaas.com / Admin123!

## PIX e cartão de crédito
A integração foi estruturada com Asaas Checkout hospedado. Copie `.env.example`, configure `ASAAS_API_KEY` e mantenha `ASAAS_SANDBOX=1` durante testes. Em produção use HTTPS, uma chave de produção e configure o webhook para `/webhooks/asaas`. Nunca coloque a chave no frontend. O sistema só ativa plano quando recebe evento de pagamento do gateway; sem credencial, cria apenas registro `configuration_required`.

## Produção
SQLite é ótimo para demonstração e teste local. Antes de escalar, migre para PostgreSQL, use reverse proxy HTTPS, armazenamento externo para uploads, backups e secret manager.


## Novidades desta versão
- Página pública redesenhada com capa, logo, cards de serviços e profissionais.
- Horários reais por profissional e dia da semana.
- Agenda visual em Dia / Semana / Mês.
- Confirmação com resumo, calendário, cancelamento e botão opcional de WhatsApp para o profissional escolhido (com fallback para o WhatsApp geral da barbearia).
- Para teste local no Windows com Python 3.14, use `requirements.txt`. Para PostgreSQL em produção, use `requirements-postgres.txt` em Python 3.13 ou ambiente compatível.

## Atualização v3 — agenda, horários, PDV e caixa
- Menu lateral destaca a área atual.
- Nova área **Horários**: jornada semanal, horários extras e bloqueios por data.
- Horários públicos removem automaticamente horários passados do dia atual.
- A duração do serviço define o passo das opções de início (ex.: 120 min -> blocos de 120 min).
- PDV e Caixa redesenhados com cards, resumo e histórico visual.
- Agenda Dia/Semana/Mês recebeu estilos visuais completos.

## V5 — vencimento e bloqueio de assinatura
O sistema possui uma camada de controle de acesso por assinatura. Trials exibem aviso nos 3 dias finais e são bloqueados no vencimento se não houver assinatura. Planos pagos exibem aviso nos 5 dias finais, têm 3 dias de carência após a data de vencimento e, depois disso, o painel é bloqueado até a regularização. A rota de cobrança permanece acessível e os dados são preservados. Em produção, a confirmação do pagamento deve vir do webhook do gateway (Asaas).


## V7 Production Candidate
Leia `PRODUCTION_CHECKLIST.md`, `STAGING.md` e `BACKUP_RESTORE.md`. Em staging/produção use `requirements-production.txt`, PostgreSQL e `scripts/start.sh`.

## V7.5 — Agenda integrada ao PDV

Fluxo operacional recomendado:

1. O atendimento é criado na Agenda.
2. O status avança até **Concluído**.
3. O atendimento aparece automaticamente em **PDV / Vendas > Atendimentos aguardando pagamento**.
4. Abra o **Caixa** antes da cobrança.
5. No PDV, escolha PIX, cartão de crédito, cartão de débito ou dinheiro e, se necessário, informe desconto.
6. Ao cobrar, o sistema cria a venda, lança a entrada no caixa e gera a comissão do profissional.
7. O mesmo atendimento não pode ser cobrado duas vezes.

A venda avulsa permanece disponível para produtos, serviços extras ou clientes sem agendamento.
