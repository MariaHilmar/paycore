# PayCore - Regras de code review

Projeto educacional de portfólio: ledger financeiro com partidas dobradas, PIX e transferências P2P idempotentes.

## Prioridades de revisão

1. **Invariantes financeiros** - saldo derivado do ledger, nunca armazenado em coluna mutável. Toda movimentação gera débito + crédito de mesmo valor.
2. **Dinheiro como inteiro** - valores em centavos (`int`). Bloquear `float`/`Decimal` em campos de valor monetário.
3. **Idempotência** - operações financeiras exigem header `Idempotency-Key`; reenvio não pode cobrar duas vezes.
4. **Concorrência** - transferências e saques devem usar `SELECT ... FOR UPDATE` na conta debitada antes de validar saldo.
5. **KYC gate** - rotas financeiras protegidas por `VerifiedAccount` (`app/api/deps.py`), não duplicar verificação nas rotas.
6. **Antifraude precede a liquidação** - fluxos de saída (`P2P`, `PIX_OUT`) passam por `PaymentService._screen` **antes** de qualquer `post_double_entry`. Uma decisão `BLOCKED`/`REVIEW` nunca pode mover dinheiro; a decisão vai para `transactions.fraud_status`.
7. **Testes** - toda nova regra de negócio (RN) precisa de teste e entrada na matriz de `docs/requisitos.md`.

## Bloquear (bug de severidade alta)

- `UPDATE accounts SET balance = ...` ou qualquer mutação direta de saldo.
- Operação financeira sem `Idempotency-Key` ou sem verificação de duplicidade.
- `float` em valores monetários.
- Transferência/saque sem lock pessimista na conta de origem.
- Rota financeira acessível por usuário não verificado (`is_verified=False`).
- Fluxo de saída (transferência/saque) que liquida sem passar pela triagem antifraude, ou que move dinheiro em decisão `BLOCKED`/`REVIEW`.
- Nova regra de antifraude adicionada ao motor sem teste em `tests/test_fraud.py`.
- Credenciais, JWT secret ou chaves hardcoded.

## Avisar (melhoria ou risco médio)

- Nova rota sem regra de negócio documentada em `docs/requisitos.md`.
- Alteração em `LedgerService` sem teste em `tests/test_ledger.py`.
- Lógica de pagamento fora de `PaymentService` (deve orquestrar, não duplicar ledger).
- Migration sem `CHECK (amount > 0)` em tabelas financeiras.

## Fora de escopo (não exigir)

- KYC com upload de documento, webhooks assíncronos, taxa de serviço (roadmap).
- Homologação BACEN ou deploy público sem hardening (`docs/SEGURANCA.md`).
