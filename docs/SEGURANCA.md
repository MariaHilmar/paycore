# Segurança e Limitações - PayCore API

> Este documento complementa o [README.md](../README.md), o [docs/requisitos.md](requisitos.md) (regras RN15, RN17 e requisitos não funcionais de segurança), o [docs/ARQUITETURA.md](ARQUITETURA.md) (seção 9 - resumo técnico) e os demais artefatos em `docs/`. Foi elaborado a partir da análise do código-fonte real (`app/`), da configuração (`app/core/`), das rotas (`app/api/`) e da suíte de testes (`tests/`), com citações de linha verificadas no repositório.

---

## Aviso

O **PayCore** é um **projeto educacional de portfólio**. Não é instituição de pagamento, PSP homologado nem sistema apto para produção financeira sem as mitigações descritas neste documento. **Não faça deploy público** sem tratar as limitações conscientes listadas na [seção 5](#5-limitações-conscientes-do-mvp).

---

## Índice

1. [Escopo e objetivo](#1-escopo-e-objetivo)
2. [Glossário de segurança](#2-glossário-de-segurança)
3. [Superfície de ataque e atores](#3-superfície-de-ataque-e-atores)
4. [Controles implementados](#4-controles-implementados)
5. [Limitações conscientes do MVP](#5-limitações-conscientes-do-mvp)
6. [Matriz de risco](#6-matriz-de-risco)
7. [Roadmap de segurança](#7-roadmap-de-segurança)
8. [Metodologia de desenvolvimento](#8-metodologia-de-desenvolvimento)
9. [Rastreabilidade controle → código → teste](#9-rastreabilidade-controle--código--teste)

---

## 1. Escopo e objetivo

O escopo de segurança deste repositório prioriza, de forma deliberada:

| Prioridade | Foco | Justificativa |
|---|---|---|
| **Alta** | Integridade financeira (ledger, idempotência, concorrência) | Diferencial técnico do projeto; erros aqui corrompem dinheiro |
| **Alta** | Autenticação básica e autorização por recurso | Proteção mínima esperada em qualquer API com dados de usuário |
| **Média** | Segredos em variáveis de ambiente, não versionados | Higiene de repositório |
| **Baixa (fora do MVP)** | Antifraude, rate limiting, webhook signing, KYC real | Planejados no roadmap; ausência documentada e justificada |

Este documento existe para **tornar explícitas** as escolhas de segurança - inclusive as ausências - de forma que revisores técnicos e recrutadores avaliem consciência de risco, não descuido.

---

## 2. Glossário de segurança

| Termo | Definição |
|---|---|
| **Autenticação** | Prova de identidade do usuário via JWT (`Bearer`) após login com e-mail e senha |
| **Autorização** | Verificação de que o usuário autenticado pode executar a ação ou acessar o recurso solicitado |
| **KYC gate** | Bloqueio de operações financeiras enquanto `user.is_verified = false` |
| **Idempotency-Key** | Header que impede dupla cobrança em retries de requisições financeiras |
| **IDOR** | *Insecure Direct Object Reference* - acesso a recurso de outro usuário manipulando identificadores |
| **Timing attack** | Inferência de segredo comparando tempos de resposta de comparações de string |
| **Webhook mock** | Endpoint `/pix/deposit/{txid}/pay` que simula callback da rede PIX sem autenticação de usuário |
| **Service-to-service key** | `X-Admin-Key` para endpoints operacionais (`/admin/*`), separada do JWT de usuário |

---

## 3. Superfície de ataque e atores

| Endpoint / área | Autenticação | Risco principal |
|---|---|---|
| `POST /auth/register`, `POST /auth/login` | Pública | Enumeração de contas, brute force de senha |
| `POST /dev/verify-me` | JWT usuário | Bypass de KYC em deploy público |
| `POST /pix/deposit`, `/withdraw`, `/transfers` | JWT + KYC + Idempotency-Key | Uso indevido por conta verificada |
| `POST /pix/deposit/{txid}/pay` | **Nenhuma** (webhook simulado) | Crédito fraudulento se `txid` for conhecido |
| `GET /transfers/{id}` | JWT + checagem origem/destino | IDOR (mitigado) |
| `GET /admin/reconciliation` | `X-Admin-Key` | Vazamento de integridade contábil |
| `GET /health`, `GET /docs` | Pública | Information disclosure (superfície da API) |

> Código (endpoints públicos documentados): `app/api/routes/auth.py` (linhas 16-49); `app/api/routes/pix.py` (linhas 73-87); `app/main.py` (linhas 8-12 - Swagger exposto pelo FastAPI).

---

## 4. Controles implementados

### SC01 - Hash de senha com bcrypt

Senhas nunca são armazenadas em texto claro. `AuthService.register` persiste apenas `password_hash` gerado por `hash_password` (bcrypt com salt por senha). O hash não é exposto em schemas de resposta (`UserOut` não inclui `password_hash`).

> Código: `hash_password`, `verify_password` (`app/core/security.py`, linhas 12-17); `AuthService.register` (`app/services/auth.py`, linhas 54-57); `UserOut` (`app/schemas/auth.py`, linhas 26-31).

### SC02 - JWT com algoritmo fixo e expiração

Tokens são emitidos com `HS256`, `sub` = UUID do usuário e `exp` configurável (`ACCESS_TOKEN_EXPIRE_MINUTES`, padrão 24h). O decode restringe `algorithms=[settings.ALGORITHM]`, mitigando troca de algoritmo na validação.

> Código: `create_access_token` (`app/core/security.py`, linhas 20-25); `decode_access_token` (linhas 28-34); `Settings.ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES` (`app/core/config.py`, linhas 19-20).

### SC03 - Rejeição segura de token malformado

`get_current_user` converte `sub` para `uuid.UUID`; valor inválido resulta em `401 Unauthorized`, não em `500 Internal Server Error`.

> Código: `get_current_user` (`app/api/deps.py`, linhas 34-38).

### SC04 - Mensagem genérica no login (anti-enumeração no login)

Credenciais inválidas retornam `401` com `detail="invalid email or password"`, sem distinguir e-mail inexistente de senha errada.

> Código: `login` (`app/api/routes/auth.py`, linhas 37-40); `InvalidCredentialsError` em `AuthService.authenticate` (`app/services/auth.py`, linhas 78-81).

### SC05 - Gate KYC centralizado para operações financeiras

Depósito, saque e transferência exigem `VerifiedAccount`, que bloqueia usuários com `is_verified = false` com `403 Forbidden`.

> Código: `get_verified_account` (`app/api/deps.py`, linhas 66-77); uso em `app/api/routes/pix.py` (parâmetro `account: VerifiedAccount`, linhas 34 e 50) e `app/api/routes/transfers.py` (linha 29).

### SC06 - Idempotency-Key obrigatória em POSTs financeiros

Ausência ou valor vazio do header retorna `400`. A chave é persistida com constraint `UNIQUE` em `transactions.idempotency_key`.

> Código: `get_idempotency_key` (`app/api/deps.py`, linhas 83-92); `Transaction.idempotency_key` (`app/db/models.py`, linhas 93-95).

### SC07 - Autorização por recurso em consulta de transferência (anti-IDOR)

`GET /transfers/{id}` retorna `404` se a conta autenticada não for origem nem destino da transferência - evita vazar existência da transação a terceiros via `403`.

> Código: `get_transfer` (`app/api/routes/transfers.py`, linhas 80-84).

### SC08 - Admin protegido por chave com comparação em tempo constante

`require_admin` compara `X-Admin-Key` com `ADMIN_API_KEY` via `secrets.compare_digest`, mitigando timing attacks na validação da chave.

> Código: `require_admin` (`app/api/deps.py`, linhas 98-109); `AdminGuard` aplicado em `app/api/routes/admin.py` (linha 7).

### SC09 - Segredos fora do versionamento

`.env` está no `.gitignore`; apenas `.env.example` com placeholders é versionado. `SECRET_KEY` e `ADMIN_API_KEY` vêm de variáveis de ambiente via `pydantic-settings`.

> Código: `.gitignore` (linha 6); `Settings` (`app/core/config.py`, linhas 17-23); `.env.example`.

### SC10 - Validação de entrada (Pydantic + constraints de banco)

Senha mínima de 8 caracteres; valores monetários estritamente positivos; CPF normalizado para 11 dígitos. Reforço no banco com `CHECK (amount > 0)`.

> Código: `UserRegister` (`app/schemas/auth.py`, linhas 9-18); `Field(gt=0)` em `app/schemas/transaction.py`; `CheckConstraint` em `app/db/models.py` (linhas 125-127, 155-157).

### SC11 - SQL parametrizado via ORM

Queries de negócio usam SQLAlchemy 2.0 (`select`, `where`) sem concatenação de SQL com entrada do usuário - mitiga SQL injection nas operações analisadas.

> Código: `LedgerService`, `PaymentService`, `AuthService` (padrão `select(...).where(...)` em todo `app/services/`).

---

## 5. Limitações conscientes do MVP

As ausências abaixo são **decisões de escopo** para um portfólio focado em ledger e transações ACID. Cada item inclui risco, justificativa e mitigação planejada para produção.

### LC01 - `/dev/verify-me` sempre disponível

| | |
|---|---|
| **Risco** | Qualquer usuário autenticado pode auto-verificar KYC e movimentar dinheiro |
| **Justificativa** | Atalho de demonstração para exercitar o fluxo completo sem pipeline de upload/análise de documentos (MinIO, SLA de revisão, compliance) |
| **Mitigação em produção** | Remover rota ou proteger com `DEBUG=true` / feature flag; substituir por máquina de estados KYC real |
| **Código** | `verify_me` (`app/api/routes/auth.py`, linhas 44-49); `AuthService.verify_user` (`app/services/auth.py`, linhas 91-97) |

### LC02 - Webhook PIX (`/pay`) sem autenticação

| | |
|---|---|
| **Risco** | Quem possuir o `txid` (UUID da transação `PENDING`) pode confirmar o depósito e creditar a conta |
| **Justificativa** | Simula callback **servidor-a-servidor** da rede PIX; usuário final não chama esse endpoint em produção real |
| **Mitigação em produção** | Assinatura HMAC do PSP, IP allowlist, secret compartilhado (`X-Webhook-Secret`), fila assíncrona |
| **Código** | `pay_deposit` (`app/api/routes/pix.py`, linhas 73-87); docstring explicita ausência de auth (linhas 75-78) |

### LC03 - Sem rate limiting

| | |
|---|---|
| **Risco** | Brute force em `/auth/login`, spam em `/auth/register`, abuso de `/pay` |
| **Justificativa** | Escopo de portfólio; infraestrutura de throttling (Redis/slowapi) adiada |
| **Mitigação em produção** | Rate limit por IP/rota; lockout temporário após N falhas de login |

### LC04 - Enumeração de e-mail/CPF no cadastro

| | |
|---|---|
| **Risco** | `409 Conflict` com `email already registered` ou `cpf already registered` revela contas existentes |
| **Justificativa** | UX de cadastro e validação explícita; trade-off aceito no MVP (login já usa mensagem genérica - SC04) |
| **Mitigação em produção** | Resposta genérica no register ou verificação assíncrona por e-mail |

> Código: `register` (`app/api/routes/auth.py`, linhas 21-28).

### LC05 - `AccountStatus.BLOCKED` não aplicado nas dependências

| | |
|---|---|
| **Risco** | Conta marcada como `BLOCKED` no modelo ainda poderia operar financeiramente |
| **Justificativa** | Status existe para extensão futura; bloqueio operacional não foi requisito do MVP |
| **Mitigação em produção** | Checar `account.status == ACTIVE` em `get_verified_account` ou dependência dedicada |
| **Código** | `AccountStatus.BLOCKED` (`app/db/models.py`, linhas 21-23); ausência de checagem em `get_verified_account` (`app/api/deps.py`, linhas 66-77) |

### LC06 - Segredos com valores default em desenvolvimento

| | |
|---|---|
| **Risco** | Deploy acidental com `SECRET_KEY` ou `ADMIN_API_KEY` previsíveis |
| **Justificativa** | Facilita `docker compose up` local sem configuração extra |
| **Mitigação em produção** | Falhar no startup se segredos forem os defaults; usar secrets manager (Railway, Vault) |
| **Código** | `Settings.SECRET_KEY`, `ADMIN_API_KEY` (`app/core/config.py`, linhas 18 e 23); `docker-compose.yml` (linhas 22-23) |

### LC07 - JWT sem refresh token nem revogação

| | |
|---|---|
| **Risco** | Token roubado permanece válido até expirar (até 24h) |
| **Justificativa** | Simplicidade do MVP; refresh/revogação adiciona tabela de sessões e complexidade |
| **Mitigação em produção** | Refresh token rotativo, blacklist de JWT, expiração curta (15-30 min) |

### LC08 - Sem antifraude, limites de valor e auditoria de segurança

| | |
|---|---|
| **Risco** | Conta comprometida pode movimentar valores ilimitados sem análise de risco |
| **Justificativa** | `FraudService` planejado no roadmap; foco atual em correção contábil, não em scoring |
| **Mitigação em produção** | Regras de velocidade, valor máximo, device fingerprint, log de eventos de segurança |

### LC09 - Swagger (`/docs`) exposto

| | |
|---|---|
| **Risco** | Information disclosure da superfície completa da API |
| **Justificativa** | Documentação interativa útil em portfólio e desenvolvimento |
| **Mitigação em produção** | Desabilitar `docs_url` quando `DEBUG=false` |

### LC10 - CPF sem validação de dígitos verificadores

| | |
|---|---|
| **Risco** | CPFs sintaticamente válidos mas matematicamente inválidos são aceitos |
| **Justificativa** | Validação de formato suficiente para MVP; algoritmo de CPF é requisito de KYC real, não de demo |
| **Mitigação em produção** | Validador de CPF com dígitos verificadores; integração com bureau de identidade |

> Código: `UserRegister.normalize_cpf` (`app/schemas/auth.py`, linhas 12-18) - valida apenas comprimento.

---

## 6. Matriz de risco

| ID | Ameaça | Probabilidade (MVP público) | Impacto | Status | Mitigação atual / planejada |
|---|---|---|---|---|---|
| R01 | Brute force em login | Média | Médio | Aberto | SC04 (mensagem genérica); roadmap: rate limit |
| R02 | Bypass KYC via `/dev/verify-me` | Alta | Alto | Aceito (MVP) | LC01 - documentado; remover em produção |
| R03 | Crédito fraudulento via `/pay` | Baixa-Média | Alto | Aceito (MVP) | LC02 - UUID dificulta adivinhação; roadmap: HMAC |
| R04 | Token JWT roubado | Baixa | Médio | Parcial | SC02 (expiração); LC07 (sem revogação) |
| R05 | IDOR em transferências | Baixa | Médio | Mitigado | SC07 |
| R06 | Vazamento de chave admin | Baixa | Alto | Parcial | SC08, SC09; LC06 (defaults) |
| R07 | SQL injection | Baixa | Alto | Mitigado | SC11 |
| R08 | Enumeração no cadastro | Média | Baixo | Aceito (MVP) | LC04 |
| R09 | Overdraft por concorrência | Média | Alto | Mitigado | Ledger + `SELECT FOR UPDATE` (RN05, RN06) |
| R10 | Crédito duplicado em webhook | Média | Alto | Mitigado | RN09; `confirm_deposit` com lock na transação |

---

## 7. Roadmap de segurança

Ordem sugerida de evolução (cada item = commit/PR visível no GitHub):

| Ordem | Entrega | Esforço estimado |
|---|---|---|
| 1 | Condicionar `/dev/verify-me` a `DEBUG=true` | Baixo |
| 2 | `X-Webhook-Secret` em `/pix/deposit/{txid}/pay` | Baixo |
| 3 | Falhar no startup com segredos default em produção | Baixo |
| 4 | Checar `AccountStatus.BLOCKED` nas dependências | Baixo |
| 5 | Rate limiting (`slowapi`) em login e register | Médio |
| 6 | `FraudService` com regras básicas | Médio |
| 7 | KYC com upload e máquina de estados | Alto |
| 8 | Webhook assíncrono com assinatura HMAC | Alto |

Itens 1-4 são **pré-requisitos mínimos** antes de qualquer deploy público.

---

## 8. Metodologia de desenvolvimento

Este projeto utilizou **ferramentas de IA generativa** sob a metodologia de **Especificação Direcionada (SDD - Specification-Driven Development)**:

1. **Especificação** - requisitos, regras de negócio (RN01-RN17), cenários BDD e ADRs definidos antes ou em paralelo à implementação (`docs/requisitos.md`, `docs/ARQUITETURA.md`).
2. **Geração assistida** - boilerplate, documentação e parte da estrutura acelerados com IA generativa.
3. **Revisão humana** - todo código passou por **code review manual** e revisão automatizada em PRs (Cursor Bugbot, regras em `.cursor/BUGBOT.md`): invariantes financeiros, concorrência, idempotência e casos de borda validados por testes automatizados (33 testes, ~88% de cobertura).
4. **Rastreabilidade** - cada regra e controle de segurança referencia arquivo e linha no repositório; matrizes de rastreabilidade ligam requisito → código → teste.

A IA acelerou a produção; a **correção e a coerência** são responsabilidade da revisão humana e da suíte de testes.

---

## 9. Rastreabilidade controle → código → teste

| Controle | Implementação | Teste automatizado |
|---|---|---|
| SC01 (bcrypt) | `app/core/security.py`, `app/services/auth.py` | Fluxo de registro em `tests/test_api.py` (`_register_verified`) |
| SC02, SC03 (JWT) | `app/core/security.py`, `app/api/deps.py` | `tests/test_api.py::test_protected_route_without_token_returns_401` |
| SC04 (login genérico) | `app/api/routes/auth.py` | `tests/test_api.py::test_login_with_wrong_password_returns_401` |
| SC05 (KYC gate) | `app/api/deps.py` (`VerifiedAccount`) | `tests/test_api.py::test_unverified_user_cannot_deposit` |
| SC06 (Idempotency-Key) | `app/api/deps.py`, `app/db/models.py` | `tests/test_api.py::test_deposit_requires_idempotency_key`; `tests/test_transfers.py::test_repeated_idempotency_key_does_not_double_charge` |
| SC07 (anti-IDOR transfer) | `app/api/routes/transfers.py` | Coberto por design; teste dedicado recomendado no roadmap |
| SC08 (admin key) | `app/api/deps.py`, `app/api/routes/admin.py` | `tests/test_api.py::test_reconciliation_requires_admin_key` |
| SC10 (validação entrada) | `app/schemas/auth.py`, `app/schemas/transaction.py` | `tests/test_api.py::test_transfer_insufficient_balance_returns_422` |
| RN09 (webhook idempotente) | `app/services/payment.py` (`confirm_deposit`) | `tests/test_deposit.py::test_concurrent_confirmation_credits_the_account_only_once` |
| LC02 (webhook sem auth) | `app/api/routes/pix.py` | Documentado; sem teste de penetração no MVP |

---

## Referências cruzadas

| Documento | Relação com segurança |
|---|---|
| [`docs/requisitos.md`](requisitos.md) | RN10 (KYC), RN15 (admin), RN17 (JWT); seção 8 (RNF segurança) |
| [`docs/ARQUITETURA.md`](ARQUITETURA.md) | Seção 9 (resumo); seção 13 (limitações) |
| [`docs/arquitetura-c4.md`](arquitetura-c4.md) | Atores externos (rede PIX simulada, operador) |
| [`docs/modelo-dados.md`](modelo-dados.md) | `password_hash`, constraints `UNIQUE`, enums de status |
