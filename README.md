# PayCore Lite

> **Projeto educacional de portfólio.** Não é instituição de pagamento nem sistema homologado. Limitações de segurança e mitigações planejadas estão documentadas em [`docs/SEGURANCA.md`](docs/SEGURANCA.md).

Sistema de ledger financeiro (fintech) enxuto — **contabilidade de partidas dobradas**, depósitos PIX e transferências P2P **idempotentes**, construído com uma stack Python de mercado.

[![CI](https://github.com/MariaHilmar/paycore/actions/workflows/ci.yml/badge.svg)](https://github.com/MariaHilmar/paycore/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.104%2B-009688)
![Tests](https://img.shields.io/badge/tests-48%20passing-brightgreen)
![Coverage](https://img.shields.io/badge/coverage-89%25-brightgreen)

---

## Índice

- [Visão geral](#visão-geral)
- [Stack](#stack)
- [Por que ledger em vez de uma coluna `saldo`?](#por-que-ledger-em-vez-de-uma-coluna-saldo)
- [Modelo de dados](#modelo-de-dados)
- [Como rodar](#como-rodar)
- [Fluxo de demonstração](#fluxo-de-demonstração)
- [Endpoints](#endpoints)
- [Testes](#testes)
- [Arquitetura](#arquitetura)
- [Documentação completa](#documentação-completa)
- [Metodologia](#metodologia)
- [Roadmap](#roadmap)

---

## Visão geral

O PayCore Lite é uma carteira digital que demonstra, em escala reduzida, os padrões que sustentam
sistemas financeiros de verdade:

- **Ledger de partidas dobradas** — todo movimento de dinheiro gera duas entradas imutáveis (um débito e um crédito de mesmo valor). O saldo nunca é armazenado: é sempre derivado do histórico.
- **Idempotência** — toda operação financeira exige um header `Idempotency-Key`; reenviar a mesma requisição nunca cobra duas vezes.
- **Transações ACID + travas de linha** — concorrência tratada com `SELECT ... FOR UPDATE`, sem overdraft nem crédito duplicado.
- **Dinheiro como inteiro (centavos)** — nunca `float`, eliminando erros de arredondamento.

---

## Stack

| Camada | Tecnologia |
|---|---|
| API | FastAPI 0.104+ |
| ORM | SQLAlchemy 2.0 (async, tipado com `Mapped[]`) |
| Validação | Pydantic v2 |
| Banco | PostgreSQL 16 |
| Migrations | Alembic |
| Auth | JWT (python-jose) + bcrypt |
| Testes | pytest + pytest-asyncio |
| Lint / Format | Ruff + Black |
| Infra | Docker Compose |

---

## Por que ledger em vez de uma coluna `saldo`?

Guardar o saldo numa coluna (`accounts.balance`) é o antipadrão mais comum em fintech. Um único
bug, race condition ou rollback mal feito corrompe o número silenciosamente, **sem rastro de auditoria**.

O PayCore usa **partidas dobradas**: cada movimento é registrado como duas linhas imutáveis em
`ledger_entries` — um `DEBIT` e um `CREDIT` de mesmo valor. Os livros sempre somam zero entre todas
as contas.

```
Alice transfere R$ 50 para Bob:

  transaction_id: abc-123
  ├── ledger_entry: DEBIT   Alice  R$ 50
  └── ledger_entry: CREDIT  Bob    R$ 50

Saldo da Alice = SUM(créditos) − SUM(débitos) das entradas dela
```

**Benefícios:**

- O saldo é **sempre** consistente com o histórico — não há como divergir.
- Trilha de auditoria completa para cada centavo.
- Sem `UPDATE accounts SET balance = balance - x` sujeito a race condition. Travamos a conta que
  paga (`SELECT FOR UPDATE`) apenas quando é necessário validar fundos.

---

## Modelo de dados

```
users
  id, email, cpf, password_hash, is_verified, created_at

accounts
  id, user_id (nullable p/ contas de sistema), account_number, status, created_at

transactions
  id, idempotency_key (UNIQUE), type(PIX_IN|P2P), status(PENDING|COMPLETED|FAILED),
  amount (centavos, CHECK > 0), metadata (JSONB), created_at, updated_at

ledger_entries
  id, transaction_id, account_id, entry_type(DEBIT|CREDIT),
  amount (centavos, CHECK > 0), created_at
```

Detalhes de design em [`docs/ARQUITETURA.md`](docs/ARQUITETURA.md).

---

## Como rodar

### Opção 1 — Docker (recomendado)

```bash
git clone https://github.com/MariaHilmar/paycore
cd paycore
docker compose up
# sobe o Postgres, roda as migrations e inicia a API em http://localhost:8000
```

Documentação interativa (Swagger): **http://localhost:8000/docs**

### Opção 2 — Local (PostgreSQL já instalado)

```bash
python -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

cp .env.example .env                # ajuste a DATABASE_URL se necessário
alembic upgrade head
uvicorn app.main:app --reload
```

---

## Fluxo de demonstração

Conta a história completa em 6 passos (`jq` e `uuidgen` opcionais, para formatação/geração de UUID):

```bash
BASE=http://localhost:8000/api/v1

# 1. Cadastrar a Alice
curl -s -X POST $BASE/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"alice@example.com","cpf":"11144477735","password":"segredo1234"}' | jq

# 2. Login → token JWT
TOKEN=$(curl -s -X POST $BASE/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"alice@example.com","password":"segredo1234"}' | jq -r .access_token)

# 3. Verificar KYC (atalho de dev)
curl -s -X POST $BASE/dev/verify-me -H "Authorization: Bearer $TOKEN" | jq

# 4. Depositar R$ 200 (cria a cobrança PIX e simula o pagamento)
DEPOSITO=$(curl -s -X POST $BASE/pix/deposit \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $(uuidgen)" \
  -d '{"amount_cents":20000}')
echo $DEPOSITO | jq
TXID=$(echo $DEPOSITO | jq -r .txid)

curl -s -X POST $BASE/pix/deposit/$TXID/pay | jq

# 5. Conferir o saldo (deve ser 20000 centavos = R$ 200,00)
curl -s $BASE/accounts/me -H "Authorization: Bearer $TOKEN" | jq

# 6. Cadastrar o Bob, verificar, e a Alice transfere R$ 50 para ele
curl -s -X POST $BASE/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"bob@example.com","cpf":"52998224725","password":"segredo1234"}' | jq

# (faça login como Bob, verifique com /dev/verify-me e pegue o account_number dele em /accounts/me)
curl -s -X POST $BASE/transfers \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $(uuidgen)" \
  -d '{"to_account_number":"<numero-da-conta-do-bob>","amount_cents":5000}' | jq
```

---

## Endpoints

| Método | Rota | Descrição |
|--------|------|-----------|
| POST | `/api/v1/auth/register` | Cria usuário + conta |
| POST | `/api/v1/auth/login` | Retorna o JWT |
| POST | `/api/v1/dev/verify-me` | Marca o usuário atual como verificado (KYC) |
| GET | `/api/v1/accounts/me` | Dados da conta + saldo em tempo real |
| GET | `/api/v1/accounts/me/statement` | Extrato paginado (histórico do ledger) |
| POST | `/api/v1/pix/deposit` | Cria cobrança PIX (retorna txid + QR mock) |
| POST | `/api/v1/pix/deposit/{txid}/pay` | Simula a liquidação PIX → credita o ledger |
| POST | `/api/v1/pix/withdraw` | Saque PIX (debita a conta / credita o settlement) |
| POST | `/api/v1/transfers` | Transferência P2P por número de conta |
| GET | `/api/v1/transfers/{id}` | Detalhe de uma transferência |
| GET | `/api/v1/admin/reconciliation` | Relatório de conciliação (header `X-Admin-Key`) |
| GET | `/api/v1/admin/fraud/reviews` | Fila de transações retidas pela antifraude (header `X-Admin-Key`) |
| POST | `/api/v1/admin/fraud/reviews/{id}/approve` | Libera uma transação retida (re-valida saldo) |
| POST | `/api/v1/admin/fraud/reviews/{id}/reject` | Rejeita uma transação retida (→ `FAILED`) |
| GET | `/health` | Health check |
| GET | `/docs` | Swagger UI |

> Todo `POST` que movimenta dinheiro exige o header `Idempotency-Key: <uuid>`.
> Os endpoints administrativos (`/admin/*`) exigem o header `X-Admin-Key: <chave>` (service-to-service, não JWT).
>
> **Antifraude:** transferências e saques passam por triagem antes de liquidar. Valores muito altos, ou que estourem
> o limite diário, são **bloqueados** (`403`); valores em faixa de revisão ficam **retidos** (`201` com `status=PENDING`
> e `fraud_status=REVIEW`) até um operador aprovar/rejeitar pela fila `/admin/fraud/reviews`.

---

## Testes

```bash
# Windows (recomendado)
.\scripts\test.ps1

# Linux/macOS/Git Bash
./scripts/test.sh

# Manual (requer PostgreSQL em localhost:5432/paycore_test)
TEST_DATABASE_URL=postgresql+psycopg://paycore:paycore@localhost:5432/paycore_test \
  pytest tests/ -v --cov=app --cov-report=term-missing
```

O script usa o Python da `.venv`, verifica conexão com o Postgres e roda `ruff`, `black --check` e `pytest`.
Opções: `-Quick` (sem cobertura), `-LintOnly`, `-SkipLint`.

### Política de revisão (CI/CD)

Push direto em `main` está **bloqueado**. Todo merge passa por **pull request** com:

1. CI verde (Lint + Test)
2. Pelo menos **1 aprovação** de revisão
3. Conversas do PR resolvidas

Configurar em outros repositórios: `.\scripts\setup-branch-protection.ps1` (requer `gh auth login` com permissão admin).

São **48 testes** (cobertura ~89%) em dois níveis:

**Testes de serviço** (lógica de domínio, direto nos services):

- **Ledger** — saldo derivado das entradas, invariante de partidas dobradas, rejeição por saldo insuficiente.
- **Depósito** — crédito só após confirmação, idempotência do `/pay`, e **dupla confirmação concorrente credita apenas uma vez**.
- **Saque** — debita a conta, é espelho do depósito (settlement volta a zero), saldo insuficiente vira `FAILED`, retry idempotente, e posta `DEBIT` no usuário / `CREDIT` no settlement.
- **Transferência** — fluxo feliz, saldo insuficiente registrado como `FAILED`, auto-transferência bloqueada, retry idempotente, e **corrida entre duas transferências nunca causa overdraft** (exercita a trava real `SELECT FOR UPDATE` no Postgres).
- **Antifraude** — cada regra isolada (valor, velocidade, limite diário), o engine escolhe a decisão **mais severa**, transação bloqueada não move dinheiro (`FAILED`), transação em revisão fica retida (`PENDING`), e a **liberação re-valida saldo** antes de liquidar (aprovar credita, rejeitar falha).
- **Conciliação** — ledger saudável reconcilia, soma global zero após depósito+saque+transferência, e **detecta um ledger adulterado** (remoção de uma perna de débito é sinalizada como discrepância).

**Testes de integração HTTP** (`tests/test_api.py`, via `httpx` + `ASGITransport`) — exercitam a stack
completa (roteamento, validação, autenticação JWT, mapeamento de erros): fluxos ponta a ponta de
depósito, saque e transferência; transferência grande **bloqueada pela antifraude** (403); transferência em
faixa de revisão **retida e depois aprovada** pela fila admin; header de idempotência obrigatório; bloqueio de
usuário não verificado (403); saldo insuficiente (422); credenciais inválidas (401); rota protegida sem token (401);
endpoints admin exigem `X-Admin-Key` (401 sem chave ou com chave incorreta).

---

## Arquitetura

```
app/
├── api/
│   ├── deps.py          # dependências: sessão, usuário atual, conta verificada, idempotency key
│   └── routes/          # auth, accounts, pix, transfers
├── core/
│   ├── config.py        # pydantic-settings (Settings única, cacheada)
│   └── security.py      # JWT encode/decode, bcrypt
├── db/
│   ├── base.py          # DeclarativeBase
│   ├── session.py       # engine async + session factory
│   └── models.py        # modelos ORM (SQLAlchemy 2.0 tipado)
├── schemas/             # modelos Pydantic v2 (request/response)
└── services/
    ├── ledger.py           # LedgerService: saldo, post_double_entry, extrato
    ├── payment.py          # PaymentService: depósito, saque, transferência, idempotência, fila de revisão
    ├── fraud.py            # FraudService: rule engine de antifraude (valor, velocidade, limite diário)
    ├── reconciliation.py   # ReconciliationService: conciliação contábil
    └── auth.py             # AuthService: registro, autenticação, verificação
```

**Decisões-chave:**

- O `LedgerService` não conhece HTTP nem Pydantic — é lógica de domínio pura, fácil de testar.
- O `PaymentService` orquestra o `LedgerService`; adicionar um `FraudService` no futuro significa
  envolver este método, sem tocar no ledger.
- Regras de negócio (verificação KYC, idempotência) ficam centralizadas em **dependências do FastAPI**,
  não espalhadas pelas rotas.
- Dinheiro é sempre `int` (centavos), com `CHECK (amount > 0)` no banco como defesa em profundidade.

---

## Documentação completa

| Documento | Conteúdo |
|---|---|
| **[`docs/requisitos.md`](docs/requisitos.md)** | Documentação **funcional**: glossário de domínio, atores, regras de negócio (RN01-RN17), histórias de usuário com critérios de aceite, cenários BDD/Gherkin, regras de validação, requisitos não funcionais e rastreabilidade requisito → código → teste |
| **[`docs/ARQUITETURA.md`](docs/ARQUITETURA.md)** | Arquitetura **técnica**: diagramas de sequência (depósito, saque, transferência), tratamento de concorrência, idempotência, segurança, padrões de projeto aplicados e ADRs (registros de decisão) |
| **[`docs/arquitetura-c4.md`](docs/arquitetura-c4.md)** | Modelo **C4** (Contexto → Contêineres → Componentes → Código): visão estrutural do sistema em camadas de abstração progressivas, com mapeamento direto para a estrutura de pastas |
| **[`docs/modelo-dados.md`](docs/modelo-dados.md)** | **Modelo de dados**: diagrama ER, dicionário de dados completo (tabela por tabela, coluna por coluna), enums nativos, índices/constraints e histórico de migrações |
| **[`docs/SEGURANCA.md`](docs/SEGURANCA.md)** | **Segurança e limitações**: controles implementados, riscos conscientes do MVP, matriz de risco, roadmap de hardening e rastreabilidade controle → código → teste |

**Case study no portfólio:** [mariahilmar.vercel.app/docs/case-study-paycore](https://mariahilmar.vercel.app/docs/case-study-paycore)

---

## Metodologia

Este projeto foi desenvolvido com **ferramentas de IA generativa** sob a metodologia de **Especificação Direcionada (SDD)**: requisitos, regras de negócio e cenários BDD foram definidos antes da implementação (`docs/requisitos.md`), e o código passou por **revisão humana rigorosa** (code review, testes unitários e de integração com ~89% de cobertura). A IA acelerou boilerplate e documentação; a correção financeira e a coerência arquitetural são validadas pela suíte de testes e pela rastreabilidade requisito → código → teste documentada em `docs/`.

### Code review

- **Revisão humana** em pull requests (template em [`.github/pull_request_template.md`](.github/pull_request_template.md))
- **Cursor Bugbot** em PRs com regras financeiras em [`.cursor/BUGBOT.md`](.cursor/BUGBOT.md) (ledger, idempotência, concorrência, KYC)
- Ative o Bugbot no [dashboard do Cursor](https://cursor.com/dashboard) para o repositório `MariaHilmar/paycore`

---

## Roadmap

### MVP (entregue)

- [x] **Cadastro, login JWT e gate KYC** (`/dev/verify-me`)
- [x] **Ledger de partidas dobradas** - saldo derivado do histórico, dinheiro em centavos (`int`)
- [x] **Depósito PIX** (`PIX_IN`) - criação de cobrança + confirmação (`/pay`) com idempotência
- [x] **Transferências P2P** - débito/crédito entre contas com `SELECT FOR UPDATE` e `Idempotency-Key`
- [x] **Saldo e extrato** - consulta derivada do ledger
- [x] **Infra e qualidade** - Docker Compose, migrations Alembic, CI e documentação SDD

### Evolução 1 (entregue)

- [x] **Saque PIX** (tipo `PIX_OUT`) - debita a conta, espelho do depósito
- [x] **Conciliação (admin)** - cruza ledger × transações, prova a soma-zero e detecta drift

### Evolução 2 (entregue)

- [x] **Motor de antifraude** (`FraudService`) - rule engine (valor, velocidade, limite diário) rodando como gate antes da liquidação, com fila de revisão manual (`/admin/fraud/reviews`)

### Próximas evoluções

- [ ] KYC com upload de documento (substitui a flag `is_verified` por uma máquina de estados)
- [ ] Webhooks assíncronos (o `/pay` vira um job em Redis; tabela de eventos de webhook)
- [ ] Taxa de serviço (novo tipo `FEE`; o ledger já suporta nativamente)

---

## Licença

MIT - veja o arquivo [LICENSE](LICENSE) para detalhes.
