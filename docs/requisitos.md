# Documentação Técnica e Funcional - PayCore API

> Este documento complementa o [README.md](../README.md) (visão geral, stack, execução), o [docs/ARQUITETURA.md](ARQUITETURA.md) (arquitetura, ADRs, diagramas de sequência) e o [docs/SEGURANCA.md](SEGURANCA.md) (controles de segurança e limitações do MVP) com a análise de requisitos do sistema: domínio, regras de negócio, histórias de usuário, critérios de aceite e cenários BDD. Foi elaborado a partir da análise do código-fonte real (`app/`), migrations (`alembic/`) e suíte de testes (`tests/`) existentes, mantendo os mesmos termos e nomenclaturas já usados no projeto.

---

## Índice

1. [Visão do produto](#1-visão-do-produto)
2. [Glossário de domínio](#2-glossário-de-domínio)
3. [Atores e integrações](#3-atores-e-integrações)
4. [Regras de negócio](#4-regras-de-negócio)
5. [Histórias de usuário](#5-histórias-de-usuário)
6. [Cenários BDD (Gherkin)](#6-cenários-bdd-gherkin)
7. [Regras de validação de dados](#7-regras-de-validação-de-dados)
8. [Requisitos não funcionais](#8-requisitos-não-funcionais)
9. [Rastreabilidade requisito -> código -> teste](#9-rastreabilidade-requisito---código---teste)

---

## 1. Visão do produto

O **PayCore** é um sistema de carteira digital (fintech) que oferece cadastro de contas, depósito e saque via PIX (simulado) e transferências P2P entre contas, com contabilidade baseada em **partidas dobradas** (double-entry bookkeeping) - o mesmo princípio contábil usado por bancos e processadoras de pagamento reais.

**Problema que resolve:** sistemas financeiros que armazenam saldo como uma coluna mutável (`accounts.balance`) estão sujeitos a corrupção silenciosa por bugs, race conditions ou rollbacks parciais, e não oferecem trilha de auditoria. O PayCore resolve isso derivando o saldo, a qualquer momento, de um log imutável de lançamentos contábeis (`ledger_entries`), garantindo que o dinheiro nunca seja criado, destruído ou perdido - apenas movido entre contas.

**Fora de escopo (não implementado):** integração real com a rede PIX do Banco Central (o depósito e o saque são simulados/mockados), KYC com upload e análise de documento (existe apenas uma flag booleana `is_verified` e um endpoint de atalho para ambiente de desenvolvimento), cobrança de taxas de serviço (`FEE`), webhooks assíncronos (a confirmação de depósito é síncrona), notificações ao usuário.

---

## 2. Glossário de domínio

| Termo | Definição |
|---|---|
| **Conta (`Account`)** | Entidade que representa a carteira de um usuário, identificada por um `account_number` numérico de 10 dígitos, gerado aleatoriamente |
| **Conta de settlement** | Conta de sistema (`account_number = "0000000000"`, sem `user_id`) que funciona como contrapartida de todo dinheiro que entra ou sai via PIX. Garante que o sistema como um todo permaneça em soma-zero |
| **Transação (`Transaction`)** | A intenção de negócio de um movimento financeiro (depositar, sacar, transferir), com um `status` (`PENDING`, `COMPLETED`, `FAILED`) e um `type` (`PIX_IN`, `PIX_OUT`, `P2P`) |
| **Lançamento (`LedgerEntry`)** | O efeito contábil que uma transação produz: uma entrada de débito ou crédito, de valor sempre positivo, associada a uma conta |
| **Partidas dobradas (double-entry)** | Princípio contábil pelo qual toda transação gera exatamente um débito e um crédito de mesmo valor. O saldo de uma conta é sempre `Σ créditos - Σ débitos`, nunca um valor armazenado |
| **Idempotência** | Propriedade pela qual repetir a mesma requisição financeira (mesma `Idempotency-Key`) nunca duplica o efeito - a mesma transação é retornada |
| **Conciliação (reconciliation)** | Processo de cruzar os lançamentos do ledger com as transações para provar que os livros contábeis são consistentes: soma-zero global e balanço por transação |
| **Antifraude (fraud screening)** | Triagem aplicada a transações de saída (saque, transferência) **antes** de o dinheiro se mover, por um motor de regras que devolve uma decisão: `APPROVED`, `REVIEW` (retida para análise manual) ou `BLOCKED` (recusada) |
| **`fraud_status`** | Coluna que registra a decisão da antifraude para uma transação (`APPROVED`/`REVIEW`/`BLOCKED`); ortogonal ao `status` do ciclo de vida (uma transação pode ser `PENDING`/`REVIEW`, `FAILED`/`BLOCKED` ou `COMPLETED`/`APPROVED`) |
| **Fila de revisão** | Conjunto de transações retidas (`status=PENDING`, `fraud_status=REVIEW`) aguardando um operador aprovar (liberar e liquidar) ou rejeitar |
| **KYC (Know Your Customer)** | Processo de verificação de identidade do usuário. No PayCore é representado pela flag `is_verified` em `User`, ativada via endpoint de atalho de desenvolvimento (`/dev/verify-me`) |
| **PIX** | Sistema de pagamentos instantâneos brasileiro. No PayCore, depósito e saque via PIX são **simulados** (não há integração real com o Banco Central) |
| **Centavo (cents)** | Unidade monetária interna do sistema: todo valor é armazenado e trafegado como número inteiro de centavos, nunca como ponto flutuante |

---

## 3. Atores e integrações

| Ator | Tipo | Interação |
|---|---|---|
| **Usuário final** | Ator humano | Consome os endpoints de autenticação, conta, PIX e transferências (`/api/v1/auth/*`, `/api/v1/accounts/*`, `/api/v1/pix/*`, `/api/v1/transfers/*`), autenticado via JWT |
| **Operador/Ops** | Ator humano (interno) | Consome o endpoint administrativo de conciliação (`/api/v1/admin/reconciliation`), autenticado via chave de serviço (`X-Admin-Key`) |
| **Rede PIX (simulada)** | Sistema externo (mockado) | O endpoint `POST /pix/deposit/{txid}/pay` faz o papel de um callback/webhook da rede PIX confirmando a liquidação de um depósito. Não há integração real |
| **Banco de dados** | Infraestrutura | PostgreSQL 16, acessado via SQLAlchemy 2.0 assíncrono (driver `psycopg` 3) |

Não há autenticação de usuário final nos endpoints `GET /health` e `POST /pix/deposit/{txid}/pay` - o primeiro é público por design (health check); o segundo é intencionalmente não autenticado por simular um callback servidor-a-servidor, não uma ação de usuário.

---

## 4. Regras de negócio

As regras abaixo foram extraídas do comportamento real implementado em `app/services/ledger.py`, `app/services/payment.py`, `app/services/auth.py`, `app/services/reconciliation.py`, `app/services/fraud.py` e `app/api/`.

### RN01 - Unicidade de e-mail, CPF e número de conta
Cada `User` é identificado de forma única por `email` e por `cpf` (constraints `UNIQUE` no banco). Cada `Account` possui um `account_number` único de 10 dígitos, gerado por `generate_account_number()` com até 5 tentativas de checagem de colisão antes de persistir.
> Código: `app/db/models.py` (`unique=True` em `email`, `cpf`, `account_number`); `AuthService.register` (`app/services/auth.py`, linhas 48-76).

### RN02 - Saldo é sempre derivado do ledger, nunca armazenado
Não existe coluna `balance` em `Account`. O saldo de qualquer conta é calculado, a qualquer momento, como `Σ(lançamentos CREDIT) - Σ(lançamentos DEBIT)` sobre a tabela `ledger_entries`. Isso torna impossível o saldo divergir do seu histórico.
> Código: `LedgerService.get_balance` (`app/services/ledger.py`, linhas 46-61).

### RN03 - Toda transação gera exatamente um par de lançamentos (débito e crédito)
`LedgerService.post_double_entry` sempre insere duas linhas em `ledger_entries` para a mesma `transaction_id`: uma `DEBIT` e uma `CREDIT`, de mesmo valor (`amount`). O valor do lançamento é sempre positivo; a direção (entrada/saída) é dada exclusivamente pelo `entry_type`, nunca pelo sinal do número.
> Código: `LedgerService.post_double_entry` (`app/services/ledger.py`, linhas 84-132); reforçado por `CHECK (amount > 0)` em `ledger_entries` (migração `acf930e39d05`).

### RN04 - Conta de settlement como contrapartida de dinheiro externo
Depósitos e saques via PIX não creditam/debitam a conta do usuário isoladamente - eles sempre têm como contrapartida a **conta de settlement** (`account_number = "0000000000"`, `user_id = NULL`), criada sob demanda (`get_or_create_settlement_account`). Depósito: `DEBIT` settlement / `CREDIT` usuário. Saque: `DEBIT` usuário / `CREDIT` settlement. Isso garante que o sistema como um todo nunca cria ou destrói dinheiro, apenas o move.
> Código: `LedgerService.get_or_create_settlement_account` (linhas 63-73); `PaymentService.confirm_deposit` (linhas 91-100) e `PaymentService.create_withdrawal` (linhas 119-146) em `app/services/payment.py`.

### RN05 - Transferência e saque exigem saldo suficiente; depósito não
Ao debitar uma conta (transferência ou saque), o sistema trava a linha da conta pagadora (`SELECT ... FOR UPDATE`) e verifica se o saldo é suficiente **antes** de lançar os lançamentos. Se insuficiente, a operação é recusada (`InsufficientBalanceError`) e nenhum lançamento é criado. O depósito, ao debitar a conta de settlement, **não** exige saldo suficiente (`enforce_sufficient_funds=False`) - a conta de settlement pode ficar negativa, pois representa dinheiro que entra de fora do sistema.
> Código: `LedgerService.post_double_entry`, parâmetro `enforce_sufficient_funds` (`app/services/ledger.py`, linhas 84-115).

### RN06 - Trava de linha apenas na conta debitada (não em ambas)
Diferente de uma implementação ingênua que travaria as duas contas envolvidas, o PayCore trava **apenas a conta que está sendo debitada**, e somente quando há verificação de saldo. Creditar uma conta nunca precisa de trava, pois só pode aumentar o saldo. Essa escolha evita gargalo global na conta de settlement (compartilhada por todos os depósitos) e elimina qualquer possibilidade de deadlock por ordenação de locks, pois no máximo uma linha é travada por operação.
> Código: `LedgerService.post_double_entry` (`app/services/ledger.py`, linhas 111-115); `LedgerService._lock_account` (linhas 75-82).

### RN07 - Transação recusada por saldo insuficiente é registrada como FAILED
Quando uma transferência ou saque falha por saldo insuficiente, a transação **não desaparece**: seu `status` é atualizado para `FAILED` e o commit é efetivado, preservando a tentativa no histórico para fins de auditoria, antes de a exceção ser propagada para a camada de API.
> Código: bloco `except InsufficientBalanceError` em `PaymentService.create_transfer` (`app/services/payment.py`, linhas 204-207) e `PaymentService.create_withdrawal` (linhas 147-150).

### RN08 - Idempotência obrigatória via `Idempotency-Key`
Toda operação financeira (depósito, saque, transferência) exige um header `Idempotency-Key`. A chave é persistida em `transactions.idempotency_key` com constraint `UNIQUE`. Se a mesma chave for reenviada, a transação já existente é retornada sem processar novamente - mesmo sob corrida (o `IntegrityError` da constraint `UNIQUE` é capturado, a sessão é revertida e a transação existente é relida).
> Código: `PaymentService.create_deposit`, `create_withdrawal`, `create_transfer` (`app/services/payment.py`); `get_idempotency_key` (`app/api/deps.py`, linhas 83-95).

### RN09 - Confirmação de depósito é idempotente mesmo sob concorrência
O depósito PIX é um fluxo em duas fases: `POST /pix/deposit` cria uma cobrança `PENDING`; `POST /pix/deposit/{txid}/pay` confirma e credita de fato. A confirmação trava a linha da própria transação (`SELECT ... FOR UPDATE`) antes de checar o status - se duas confirmações chegarem simultaneamente (retry de webhook), a segunda aguarda a primeira, relê `status = COMPLETED` e retorna sem creditar novamente.
> Código: `PaymentService.confirm_deposit` (`app/services/payment.py`, linhas 73-104).

### RN10 - Verificação KYC (`is_verified`) é obrigatória para movimentar dinheiro
Nenhuma conta pode depositar, sacar ou transferir dinheiro enquanto o usuário não estiver marcado como verificado (`user.is_verified = True`). A verificação, no MVP, é feita via `POST /dev/verify-me` (atalho de desenvolvimento, sem upload de documento). A regra é centralizada em uma única dependência do FastAPI para não divergir entre as rotas de PIX e transferência.
> Código: `get_verified_account` / `VerifiedAccount` (`app/api/deps.py`, linhas 66-80).

### RN11 - Conta destino de transferência deve existir e ser diferente da origem
Uma transferência exige que o `to_account_number` corresponda a uma conta existente (`AccountNotFoundError` caso contrário) e que não seja a mesma conta de origem (`SelfTransferError`).
> Código: `PaymentService.create_transfer` (`app/services/payment.py`, linhas 169-173).

### RN12 - Valor monetário sempre inteiro em centavos e sempre positivo
Todo campo monetário (`amount` em `Transaction` e `LedgerEntry`, `amount_cents` nos schemas de API) é um número inteiro representando centavos - nunca ponto flutuante. Valores não positivos são rejeitados tanto na validação de entrada (Pydantic, `Field(gt=0)`) quanto no banco (`CHECK (amount > 0)`).
> Código: `app/schemas/transaction.py` (`Field(gt=0)`); `app/db/models.py` (`CheckConstraint("amount > 0", ...)` em `Transaction` e `LedgerEntry`); migração `acf930e39d05`.

### RN13 - Extrato e saldo refletem apenas o histórico local
O extrato (`GET /accounts/me/statement`) é uma listagem paginada dos lançamentos (`ledger_entries`) da conta do usuário autenticado, ordenados do mais recente para o mais antigo, sem qualquer chamada a sistemas externos - o PayCore não integra com um provedor PIX real.
> Código: `LedgerService.get_statement` (`app/services/ledger.py`, linhas 134-152); `app/api/routes/accounts.py`, `get_my_statement`.

### RN14 - Conciliação verifica soma-zero global e balanço por transação
O relatório de conciliação (`GET /admin/reconciliation`) verifica dois invariantes: (1) globalmente, `Σ créditos == Σ débitos` em toda a tabela `ledger_entries`; (2) para cada transação `COMPLETED`, os totais de débito e crédito devem ser iguais entre si e iguais ao `amount` da transação. Qualquer transação que viole (2) é reportada como uma discrepância (`TransactionDiscrepancy`), e o relatório só é considerado saudável (`is_healthy`) se estiver balanceado **e** sem discrepâncias.
> Código: `ReconciliationService.run` e `_find_unbalanced_transactions` (`app/services/reconciliation.py`, linhas 61-115).

### RN15 - Endpoint de conciliação é protegido por chave de serviço, não por JWT de usuário
O endpoint `GET /admin/reconciliation` não usa autenticação JWT de usuário final - ele exige o header `X-Admin-Key`, comparado em tempo constante (`secrets.compare_digest`) contra `ADMIN_API_KEY` configurado no ambiente, para evitar vazamento de informação por *timing attack*.
> Código: `require_admin` / `AdminGuard` (`app/api/deps.py`, linhas 98-112).

### RN16 - Tipo de transação é extensível sem quebrar dados existentes
`TransactionType` é um enum nativo do PostgreSQL (`transaction_type`). Novos valores (como `PIX_OUT`, adicionado após o MVP inicial) são incorporados via `ALTER TYPE ... ADD VALUE`, preservando todas as linhas e transações já persistidas sem necessidade de migração destrutiva.
> Código: `app/db/models.py` (`TransactionType(StrEnum)`); migração `2bfdbfff13c8_add_pix_out_transaction_type.py`.

### RN17 - Autenticação via JWT com expiração
O login (`POST /auth/login`) retorna um token JWT (`HS256`) cujo `sub` é o UUID do usuário, com expiração configurável (`ACCESS_TOKEN_EXPIRE_MINUTES`, padrão 24h). Um token com `sub` malformado (não é um UUID válido) é rejeitado como credencial inválida (`401`), não como erro interno.
> Código: `create_access_token`, `decode_access_token` (`app/core/security.py`); `get_current_user` (`app/api/deps.py`, linhas 20-44).

### RN18 - Triagem antifraude precede a liquidação em fluxos de saída
Toda transferência (`P2P`) e todo saque (`PIX_OUT`) passam por um motor de regras de antifraude **antes** de qualquer lançamento no ledger (antes do `post_double_entry`). O motor executa cada regra e adota a decisão **mais severa** entre elas, seguindo a ordem `APPROVED < REVIEW < BLOCKED`. Depósitos (`PIX_IN`) não são triados (dinheiro entrando não caracteriza o risco desse motor). A decisão é persistida em `transactions.fraud_status`.
> Código: `FraudService.evaluate` (`app/services/fraud.py`); `PaymentService._screen` (`app/services/payment.py`, integrado em `create_transfer` e `create_withdrawal`).

### RN19 - As três regras de triagem: valor, velocidade e limite diário
O motor aplica três regras configuráveis (thresholds em `app/core/config.py`):
- **Valor (`AmountThresholdRule`)**: valor `>= FRAUD_BLOCK_AMOUNT_CENTS` → `BLOCKED`; valor `>= FRAUD_REVIEW_AMOUNT_CENTS` (e abaixo do bloqueio) → `REVIEW`.
- **Velocidade (`VelocityRule`)**: mais de `FRAUD_VELOCITY_MAX_DEBITS` débitos da conta na janela `FRAUD_VELOCITY_WINDOW_SECONDS` → `REVIEW`.
- **Limite diário (`DailyDebitLimitRule`)**: se os débitos das últimas 24h somados ao valor atual excedem `FRAUD_DAILY_DEBIT_LIMIT_CENTS` → `BLOCKED`.
As regras de velocidade e limite diário consultam `ledger_entries` (débitos reais, colunas indexadas `account_id`/`created_at`), refletindo dinheiro que efetivamente saiu.
> Código: `AmountThresholdRule`, `VelocityRule`, `DailyDebitLimitRule` (`app/services/fraud.py`).

### RN20 - Desfecho da triagem: bloqueio recusa, revisão retém, aprovação segue
- **`BLOCKED`**: a transação é marcada `FAILED`, nenhum dinheiro se move, e a API responde `403 Forbidden`.
- **`REVIEW`**: a transação fica **retida** (`status=PENDING`, `fraud_status=REVIEW`), sem mover dinheiro, e é devolvida ao cliente (`201` com `status=PENDING`). Fica na fila de revisão até um operador decidir.
- **`APPROVED`**: o fluxo segue normalmente para a liquidação (`post_double_entry`).
> Código: `PaymentService._screen` (`app/services/payment.py`).

### RN21 - Resolução manual de transação retida re-valida saldo na liberação
Um operador pode **aprovar** ou **rejeitar** uma transação retida via `/admin/fraud/reviews/{id}/approve|reject` (protegido por `X-Admin-Key`). Aprovar executa a liquidação **naquele momento**, com nova verificação de saldo (o saldo pode ter mudado enquanto a transação aguardava): se insuficiente, a liberação falha (`422`) e a transação vira `FAILED`. Rejeitar marca a transação como `FAILED`/`BLOCKED` sem mover dinheiro. Ambas as ações travam a linha da transação (`SELECT ... FOR UPDATE`) e exigem que ela esteja de fato retida (`PENDING`+`REVIEW`), respondendo `409 Conflict` caso contrário.
> Código: `PaymentService.approve_review`, `reject_review`, `_lock_pending_review` (`app/services/payment.py`); rotas em `app/api/routes/admin.py`.

---

## 5. Histórias de usuário

Formato: `Como <ator>, quero <ação>, para <benefício>`, com critérios de aceite testáveis.

### US01 - Cadastrar-se e obter uma conta

**Como** novo usuário,
**quero** me cadastrar informando e-mail, CPF e senha,
**para** obter automaticamente uma conta digital com número próprio, pronta para receber e movimentar dinheiro.

**Critérios de aceite:**
- Dado um e-mail, CPF e senha válidos e inéditos, ao me cadastrar, um `User` e uma `Account` vinculada são criados na mesma operação.
- Dado um e-mail já cadastrado, a API responde `409 Conflict`.
- Dado um CPF já cadastrado, a API responde `409 Conflict`.
- A conta criada recebe um `account_number` numérico de 10 dígitos, único no sistema.

### US02 - Autenticar-se e obter um token

**Como** usuário cadastrado,
**quero** fazer login com e-mail e senha,
**para** obter um token JWT que me permita acessar as demais operações da minha conta.

**Critérios de aceite:**
- Dado e-mail e senha corretos, a API retorna um `access_token` válido.
- Dado e-mail ou senha incorretos, a API responde `401 Unauthorized`.
- Um endpoint protegido acessado sem token (ou com token inválido) responde `401 Unauthorized`.

### US03 - Verificar minha conta (KYC) antes de movimentar dinheiro

**Como** usuário recém-cadastrado,
**quero** verificar minha identidade,
**para** poder depositar, sacar ou transferir dinheiro (contas não verificadas são bloqueadas por design).

**Critérios de aceite:**
- Dado um usuário não verificado, qualquer tentativa de depósito, saque ou transferência é recusada com `403 Forbidden`.
- Após chamar o endpoint de verificação, o usuário passa a ter `is_verified = true` e todas as operações financeiras são liberadas.

### US04 - Depositar dinheiro via PIX

**Como** usuário verificado,
**quero** criar uma cobrança PIX e, em seguida, confirmá-la,
**para** colocar dinheiro na minha conta digital de forma equivalente a um depósito bancário real.

**Critérios de aceite:**
- Dado um valor positivo em centavos e uma `Idempotency-Key`, a criação da cobrança retorna um `txid` e um QR code simulado, com status `PENDING`, sem alterar o saldo ainda.
- Somente após confirmar a cobrança (`POST /pix/deposit/{txid}/pay`) o saldo da conta é efetivamente creditado.
- Confirmar a mesma cobrança mais de uma vez não credita o valor duas vezes.
- Reenviar a criação da cobrança com a mesma `Idempotency-Key` retorna a cobrança já existente, sem criar uma nova.
- Um depósito sem o header `Idempotency-Key` é rejeitado com `400 Bad Request`.

### US05 - Sacar dinheiro via PIX

**Como** usuário verificado com saldo disponível,
**quero** sacar um valor da minha conta,
**para** retirar dinheiro do sistema de forma equivalente a um saque bancário real.

**Critérios de aceite:**
- Dado saldo suficiente, o saque debita a conta imediatamente e é concluído com status `COMPLETED`.
- Dado saldo insuficiente, o saque é recusado com `422 Unprocessable Entity`, e a tentativa fica registrada com status `FAILED`.
- Reenviar o saque com a mesma `Idempotency-Key` não debita o valor duas vezes.

### US06 - Transferir dinheiro para outra conta (P2P)

**Como** usuário verificado,
**quero** transferir dinheiro para outra conta do sistema informando o número da conta destino,
**para** pagar ou repassar valores a outro usuário do PayCore.

**Critérios de aceite:**
- Dado saldo suficiente e uma conta destino existente e diferente da minha, a transferência é concluída (`COMPLETED`) e o saldo é debitado da origem e creditado no destino.
- Dado saldo insuficiente, a transferência é recusada (`422`) e registrada como `FAILED`, sem afetar o saldo de nenhuma das contas.
- Dada uma conta destino inexistente, a API responde `404 Not Found`.
- Dada uma tentativa de transferir para a própria conta, a API responde `400 Bad Request`.
- Duas transferências concorrentes que, somadas, excederiam o saldo disponível: exatamente uma é concluída e a outra é recusada por saldo insuficiente - nunca ambas passam (proteção contra overdraft).

### US07 - Consultar saldo e extrato da minha conta

**Como** usuário,
**quero** consultar meu saldo atual e o histórico de lançamentos da minha conta,
**para** acompanhar minha movimentação financeira.

**Critérios de aceite:**
- `GET /accounts/me` retorna o número da conta, status e saldo atual (derivado do ledger, em centavos).
- `GET /accounts/me/statement` retorna os lançamentos da conta, paginados, ordenados do mais recente para o mais antigo, com o tipo de transação e a direção (débito/crédito) de cada lançamento.

### US08 - Consultar detalhes de uma transferência

**Como** usuário envolvido em uma transferência (remetente ou destinatário),
**quero** consultar os detalhes dessa transferência pelo seu ID,
**para** confirmar que o pagamento ou recebimento foi processado corretamente.

**Critérios de aceite:**
- Dado o ID de uma transferência da qual minha conta participa (origem ou destino), a API retorna seus detalhes completos.
- Dado o ID de uma transferência da qual minha conta **não** participa, a API responde `404 Not Found` (não vaza a existência de transferências de terceiros).
- Dado um ID que não corresponde a nenhuma transferência, a API responde `404 Not Found`.

### US09 - Auditar a integridade contábil do sistema (conciliação)

**Como** operador de operações financeiras (ops),
**quero** rodar uma verificação de conciliação sobre o ledger,
**para** provar continuamente que os livros contábeis do sistema estão corretos e identificar qualquer divergência.

**Critérios de aceite:**
- Autenticado com a chave de administração correta, o relatório de conciliação retorna se o sistema está `is_healthy` (saudável), o total de débitos e créditos globais, e a lista de transações com discrepância (se houver).
- Sem a chave de administração, ou com uma chave incorreta, a API responde `401 Unauthorized`.
- Em uma operação normal (sem manipulação indevida do banco), o relatório é sempre saudável e sem discrepâncias.
- Se uma perna de lançamento de uma transação `COMPLETED` for removida ou alterada indevidamente, a conciliação sinaliza essa transação como discrepância.

### US10 - Verificar a saúde do sistema

**Como** operador de infraestrutura (SRE/DevOps),
**quero** consultar um endpoint de health check,
**para** monitorar automaticamente a disponibilidade da API.

**Critérios de aceite:**
- O endpoint `GET /health` retorna status `200` com `{"status": "ok"}`, sem exigir autenticação.

### US11 - Barrar e revisar transações suspeitas (antifraude)

**Como** operador de risco/fraude,
**quero** que transações de saída suspeitas sejam automaticamente bloqueadas ou retidas para análise, e conseguir liberar ou rejeitar as retidas,
**para** proteger o sistema e os usuários contra movimentações fraudulentas sem travar as operações legítimas.

**Critérios de aceite:**
- Uma transferência ou saque com valor acima do teto de bloqueio (ou que estoure o limite diário) é **recusada** (`403`), sem mover dinheiro, e fica registrada como `FAILED`/`BLOCKED`.
- Uma transferência ou saque em faixa de revisão é **retida** (`201`, `status=PENDING`, `fraud_status=REVIEW`), sem mover dinheiro, e aparece na fila `/admin/fraud/reviews`.
- Autenticado com a chave de administração, posso **aprovar** uma transação retida — ela é liquidada naquele momento (com nova verificação de saldo) — ou **rejeitá-la** — marcada como `FAILED`.
- Aprovar uma transação retida cujo saldo se tornou insuficiente responde `422` e marca a transação como `FAILED`.
- Os endpoints de revisão exigem a chave de administração; sem ela, respondem `401 Unauthorized`.
- Uma transação de valor normal, dentro dos limites, é aprovada automaticamente e liquidada sem intervenção.

---

## 6. Cenários BDD (Gherkin)

Os cenários abaixo espelham o comportamento já coberto pela suíte automatizada (`tests/`), servindo como especificação executável e como referência para a rastreabilidade da seção 9.

```gherkin
Funcionalidade: Cadastro, autenticação e verificação de conta
  Como usuário do PayCore
  Quero me cadastrar, autenticar e verificar minha conta
  Para poder movimentar dinheiro com segurança

  Cenário: Cadastrar um novo usuário com sucesso
    Dado um e-mail, CPF e senha inéditos no sistema
    Quando eu solicitar o cadastro
    Então uma nova conta deve ser criada com um número de conta único
    E devo conseguir fazer login com as credenciais informadas

  Cenário: Rejeitar cadastro com e-mail duplicado
    Dado um e-mail já cadastrado por outro usuário
    Quando eu solicitar o cadastro com esse e-mail
    Então a API deve responder com status 409

  Cenário: Rejeitar login com senha incorreta
    Dado um usuário cadastrado
    Quando eu tentar fazer login com a senha errada
    Então a API deve responder com status 401

  Cenário: Bloquear movimentação financeira sem verificação KYC
    Dado um usuário cadastrado e autenticado, mas não verificado
    Quando eu tentar realizar um depósito
    Então a API deve responder com status 403

  Cenário: Acessar rota protegida sem token
    Dado que não estou autenticado
    Quando eu consultar os dados da minha conta
    Então a API deve responder com status 401
```

```gherkin
Funcionalidade: Depósito e saque via PIX (simulado)
  Como usuário verificado
  Quero depositar e sacar dinheiro via PIX
  Para movimentar minha conta digital

  Contexto:
    Dado um usuário verificado com uma conta ativa

  Cenário: Depositar dinheiro com sucesso após confirmação
    Quando eu criar uma cobrança PIX de depósito com uma Idempotency-Key
    Então a cobrança deve ser criada com status "PENDING" e meu saldo não deve mudar
    Quando eu confirmar o pagamento dessa cobrança
    Então a cobrança deve passar para o status "COMPLETED"
    E meu saldo deve ser creditado no valor depositado

  Cenário: Confirmar o mesmo depósito duas vezes não duplica o crédito
    Dado um depósito já confirmado com sucesso
    Quando eu confirmar o pagamento da mesma cobrança novamente
    Então meu saldo deve permanecer inalterado após a segunda confirmação

  Cenário: Criar depósito sem header de idempotência
    Quando eu solicitar um depósito sem o header Idempotency-Key
    Então a API deve responder com status 400

  Cenário: Sacar dinheiro com saldo suficiente
    Dado que minha conta tem saldo disponível maior que o valor do saque
    Quando eu solicitar o saque desse valor
    Então o saque deve ser concluído com status "COMPLETED"
    E meu saldo deve ser debitado no valor sacado

  Cenário: Rejeitar saque com saldo insuficiente
    Dado que minha conta tem saldo menor que o valor do saque solicitado
    Quando eu solicitar o saque
    Então a API deve responder com status 422
    E a tentativa de saque deve ficar registrada com status "FAILED"
    E meu saldo não deve ser alterado
```

```gherkin
Funcionalidade: Transferência P2P entre contas
  Como usuário verificado
  Quero transferir dinheiro para outra conta do sistema
  Para pagar ou repassar valores a outro usuário

  Cenário: Transferir com sucesso entre duas contas
    Dado que minha conta tem saldo suficiente
    E existe uma conta destino válida e diferente da minha
    Quando eu solicitar a transferência do valor
    Então a transferência deve ser concluída com status "COMPLETED"
    E minha conta deve ser debitada e a conta destino creditada no mesmo valor

  Cenário: Rejeitar transferência com saldo insuficiente
    Dado que minha conta tem saldo menor que o valor da transferência
    Quando eu solicitar a transferência
    Então a API deve responder com status 422
    E a transferência deve ficar registrada com status "FAILED"

  Cenário: Rejeitar transferência para conta inexistente
    Quando eu solicitar uma transferência para um número de conta que não existe
    Então a API deve responder com status 404

  Cenário: Rejeitar transferência para a própria conta
    Quando eu solicitar uma transferência para o meu próprio número de conta
    Então a API deve responder com status 400

  Cenário: Duas transferências concorrentes nunca causam saldo negativo
    Dado que minha conta tem saldo de R$ 100,00
    Quando duas transferências de R$ 60,00 forem solicitadas simultaneamente para contas diferentes
    Então exatamente uma delas deve ser concluída
    E a outra deve ser recusada por saldo insuficiente
    E meu saldo final deve ser exatamente R$ 40,00

  Cenário: Reenviar transferência com a mesma chave de idempotência
    Dado que já solicitei uma transferência com uma determinada Idempotency-Key
    Quando eu reenviar a mesma requisição com a mesma Idempotency-Key
    Então a transferência original deve ser retornada
    E meu saldo não deve ser debitado uma segunda vez
```

```gherkin
Funcionalidade: Triagem antifraude de transações de saída
  Como operador de risco/fraude
  Quero que transações suspeitas sejam bloqueadas ou retidas antes de liquidar
  Para proteger o sistema sem travar operações legítimas

  Cenário: Bloquear uma transferência de valor muito alto
    Dado um usuário verificado
    Quando ele solicitar uma transferência de valor acima do teto de bloqueio
    Então a API deve responder com status 403
    E nenhum dinheiro deve se mover

  Cenário: Reter uma transferência em faixa de revisão
    Dado um usuário verificado com saldo suficiente
    Quando ele solicitar uma transferência em faixa de revisão
    Então a resposta deve ter status 201 com status "PENDING" e fraud_status "REVIEW"
    E o saldo da conta não deve mudar enquanto a transação estiver retida
    E a transação deve aparecer na fila de revisão do admin

  Cenário: Aprovar uma transação retida liquida o movimento
    Dado uma transferência retida na fila de revisão
    Quando um operador aprovar essa transação com a chave de administração
    Então a transação deve passar para "COMPLETED"
    E o dinheiro deve ser efetivamente movido entre as contas

  Cenário: Rejeitar uma transação retida não move dinheiro
    Dado uma transferência retida na fila de revisão
    Quando um operador rejeitar essa transação com a chave de administração
    Então a transação deve passar para "FAILED"
    E o saldo das contas não deve mudar

  Cenário: Bloquear acesso à fila de revisão sem a chave de administração
    Quando eu consultar a fila de revisão sem informar o header X-Admin-Key
    Então a API deve responder com status 401

  Cenário: Uma transação normal é aprovada automaticamente
    Dado um usuário verificado com saldo suficiente
    Quando ele solicitar uma transferência de valor dentro dos limites
    Então a transferência deve ser concluída (COMPLETED) sem intervenção manual
```

```gherkin
Funcionalidade: Conciliação contábil (ledger)
  Como operador de operações financeiras
  Quero auditar a integridade contábil do sistema
  Para garantir que os livros contábeis estão corretos

  Cenário: Relatório de conciliação saudável após operações normais
    Dado que foram realizados depósitos, saques e transferências normalmente
    Quando eu consultar o relatório de conciliação com a chave de administração correta
    Então o relatório deve indicar que o sistema está saudável (is_healthy = true)
    E o total de débitos deve ser igual ao total de créditos
    E não deve haver nenhuma discrepância listada

  Cenário: Detectar uma transação com lançamento corrompido
    Dado uma transferência concluída com seus dois lançamentos (débito e crédito) persistidos
    Quando um dos lançamentos dessa transferência for removido indevidamente do banco
    E eu consultar o relatório de conciliação
    Então o relatório deve indicar que o sistema não está saudável
    E a transação afetada deve constar na lista de discrepâncias

  Cenário: Bloquear acesso à conciliação sem a chave de administração
    Quando eu consultar o relatório de conciliação sem informar o header X-Admin-Key
    Então a API deve responder com status 401

  Cenário: Bloquear acesso à conciliação com chave incorreta
    Quando eu consultar o relatório de conciliação com uma chave de administração inválida
    Então a API deve responder com status 401
```

```gherkin
Funcionalidade: Observabilidade e saúde do sistema
  Como operador de infraestrutura
  Quero monitorar a saúde da API
  Para agir preventivamente em caso de indisponibilidade

  Cenário: Sistema saudável
    Quando eu consultar o endpoint de health check
    Então a API deve responder com status 200
    E o corpo da resposta deve indicar status "ok"
```

---

## 7. Regras de validação de dados

| Campo | Regra | Origem |
|---|---|---|
| `email` (cadastro) | Formato de e-mail válido (`EmailStr`) | `UserRegister` em `app/schemas/auth.py` |
| `cpf` (cadastro) | Normalizado (remoção de máscara) e deve conter exatamente 11 dígitos | `UserRegister.normalize_cpf` (`app/schemas/auth.py`, linhas 12-18) |
| `password` (cadastro) | Entre 8 e 128 caracteres | `UserRegister.password` (`Field(min_length=8, max_length=128)`) |
| `amount_cents` (depósito, saque, transferência) | Inteiro estritamente positivo (`> 0`), representando centavos | `Field(gt=0)` em `app/schemas/transaction.py`; reforçado por `CHECK (amount > 0)` no banco |
| `to_account_number` (transferência) | String obrigatória, entre 1 e 20 caracteres | `TransferCreate.to_account_number` (`app/schemas/transaction.py`) |
| `Idempotency-Key` (header) | Obrigatório e não-vazio em todo `POST` financeiro (`/pix/deposit`, `/pix/withdraw`, `/transfers`) | `get_idempotency_key` (`app/api/deps.py`, linhas 83-95) |
| `X-Admin-Key` (header) | Obrigatório e deve corresponder exatamente (comparação em tempo constante) a `ADMIN_API_KEY` para acessar `/admin/*` | `require_admin` (`app/api/deps.py`, linhas 98-109) |
| `page`, `page_size` (extrato) | Inteiros positivos; `page_size` limitado a no máximo 100 | `app/api/routes/accounts.py::get_my_statement` (`Query(default=1, ge=1)`, `Query(default=20, ge=1, le=100)`) |
| `account_number` (conta) | Numérico, 10 dígitos, gerado pelo sistema; unicidade garantida com nova tentativa em caso de colisão | `generate_account_number` (`app/services/auth.py`, linhas 32-33) |
| Thresholds de antifraude (`FRAUD_*`) | Configuráveis por ambiente (valores em centavos): faixa de revisão, teto de bloqueio, janela e máximo de velocidade, limite diário | `Settings` (`app/core/config.py`); `.env.example` |

---

## 8. Requisitos não funcionais

| Categoria | Requisito | Evidência no projeto |
|---|---|---|
| **Consistência/Atomicidade** | Nenhuma operação financeira pode deixar lançamentos parciais: ou os dois lados (débito e crédito) são persistidos, ou nenhum | RN03, RN07; `LedgerService.post_double_entry` insere sempre os dois lançamentos na mesma transação de banco |
| **Idempotência** | Repetir uma operação financeira (mesma `Idempotency-Key`, ou confirmar o mesmo depósito duas vezes) nunca duplica o efeito | RN08, RN09; `tests/test_deposit.py`, `tests/test_transfers.py`, `tests/test_withdrawal.py` |
| **Isolamento sob concorrência** | Transferências e saques concorrentes sobre a mesma conta nunca resultam em saldo negativo (overdraft) | RN06; `tests/test_transfers.py::test_concurrent_transfers_never_overdraft_the_source_account` |
| **Correção monetária** | Dinheiro nunca é representado como ponto flutuante; sempre inteiro em centavos, com valor positivo garantido em dois níveis (aplicação e banco) | RN12 |
| **Auditabilidade** | Toda tentativa de movimentação financeira (mesmo recusada) fica registrada com status rastreável (`PENDING`/`COMPLETED`/`FAILED`) | RN07; extrato (`GET /accounts/me/statement`) |
| **Verificabilidade contínua** | O sistema oferece um mecanismo ativo (conciliação) para provar sua própria integridade contábil, não apenas confiar na lógica de aplicação | RN14; `tests/test_reconciliation.py` |
| **Prevenção de fraude** | Transações de saída suspeitas são bloqueadas ou retidas antes de liquidar, com decisão persistida e fila de revisão manual | RN18-RN21; `tests/test_fraud.py` |
| **Extensibilidade sem migração destrutiva** | Novos tipos de transação e colunas de decisão são incorporados sem reescrever dados existentes | RN16; migrações `2bfdbfff13c8`, `a82ae7b338f7` |
| **Segurança** | Senhas com hash `bcrypt`; tokens JWT com expiração; comparação de chave administrativa em tempo constante | RN15, RN17; `app/core/security.py`; detalhes em [`docs/SEGURANCA.md`](SEGURANCA.md) |
| **Qualidade de código** | Lint (Ruff) obrigatório, sem exceções pendentes | `pyproject.toml` (`[tool.ruff]`); execução `ruff check` sem erros |
| **Cobertura de testes** | Suíte cobre lógica de domínio (services) e a stack HTTP completa (rotas, validação, autenticação) | 48 testes, ~89% de cobertura (`pytest --cov=app`) |
| **Portabilidade de execução** | Deve rodar via Docker Compose (Postgres + API) sem configuração manual adicional | `docker-compose.yml`, `Dockerfile` |
| **Segurança de segredos** | Credenciais e chaves nunca versionadas; apenas exemplo vazio | `.gitignore`, `.env.example` |

---

## 9. Rastreabilidade requisito -> código -> teste

| Regra/História | Implementação | Teste automatizado |
|---|---|---|
| RN01 (unicidade e cadastro) | `AuthService.register` (`app/services/auth.py`) | `tests/test_api.py::test_login_with_wrong_password_returns_401` (fluxo de registro reutilizado em `_register_verified`) |
| RN02 (saldo derivado do ledger) | `LedgerService.get_balance` | `tests/test_ledger.py::test_balance_is_derived_from_ledger_entries` |
| RN03 (par débito/crédito) | `LedgerService.post_double_entry` | `tests/test_ledger.py::test_double_entry_transfer_keeps_books_balanced` |
| RN04 (conta de settlement) | `LedgerService.get_or_create_settlement_account`; `PaymentService.confirm_deposit`, `create_withdrawal` | `tests/test_ledger.py::test_settlement_account_can_go_negative_for_deposits`; `tests/test_withdrawal.py::test_withdrawal_is_mirror_of_deposit_on_settlement` |
| RN05, RN06 (trava e saldo suficiente) | `LedgerService.post_double_entry` (parâmetro `enforce_sufficient_funds`) | `tests/test_ledger.py::test_transfer_rejected_when_balance_insufficient`; `tests/test_transfers.py::test_concurrent_transfers_never_overdraft_the_source_account` |
| RN07 (FAILED preserva auditoria) | `PaymentService.create_transfer`, `create_withdrawal` (bloco `except InsufficientBalanceError`) | `tests/test_transfers.py::test_transfer_with_insufficient_balance_is_recorded_as_failed`; `tests/test_withdrawal.py::test_withdrawal_with_insufficient_balance_is_recorded_as_failed` |
| RN08 (idempotência por chave) | `PaymentService.create_deposit/create_withdrawal/create_transfer` | `tests/test_deposit.py::test_creating_deposit_twice_with_same_key_returns_same_transaction`; `tests/test_transfers.py::test_repeated_idempotency_key_does_not_double_charge`; `tests/test_withdrawal.py::test_withdrawal_is_idempotent` |
| RN09 (confirmação concorrente) | `PaymentService.confirm_deposit` (`SELECT ... FOR UPDATE`) | `tests/test_deposit.py::test_concurrent_confirmation_credits_the_account_only_once` |
| RN10, US03 (verificação KYC) | `get_verified_account` / `VerifiedAccount` (`app/api/deps.py`) | `tests/test_api.py::test_unverified_user_cannot_deposit` |
| RN11, US06 (validação de destino) | `PaymentService.create_transfer` | `tests/test_transfers.py::test_transfer_to_self_is_rejected`, `test_transfer_to_unknown_account_number_raises` |
| RN12 (dinheiro em centavos, positivo) | `app/schemas/transaction.py`, `app/db/models.py` (`CheckConstraint`) | Validação de schema via Pydantic (`Field(gt=0)`); constraint reforçada em produção |
| RN13, US07 (extrato) | `LedgerService.get_statement`; `app/api/routes/accounts.py` | Coberto indiretamente por `tests/test_withdrawal.py::test_withdrawal_posts_debit_on_user_credit_on_settlement` (usa `get_statement`) |
| RN14, US09 (conciliação) | `ReconciliationService.run`, `_find_unbalanced_transactions` | `tests/test_reconciliation.py::test_healthy_ledger_reconciles`, `test_global_sum_is_zero_across_all_flows`, `test_reconciliation_detects_a_tampered_ledger` |
| RN15 (guarda de admin) | `require_admin` / `AdminGuard` (`app/api/deps.py`) | `tests/test_api.py::test_reconciliation_requires_admin_key` |
| RN16 (enum extensível) | `TransactionType(StrEnum)`; migração `2bfdbfff13c8` | Migração validada manualmente do zero (banco descartável); coberta indiretamente por `tests/test_withdrawal.py` |
| RN17, US02 (autenticação JWT) | `create_access_token`, `decode_access_token`, `get_current_user` | `tests/test_api.py::test_login_with_wrong_password_returns_401`, `test_protected_route_without_token_returns_401` |
| RN18, RN19 (motor e regras de fraude) | `FraudService.evaluate`; `AmountThresholdRule`, `VelocityRule`, `DailyDebitLimitRule` (`app/services/fraud.py`) | `tests/test_fraud.py::test_amount_rule_*`, `test_engine_takes_the_most_severe_outcome`, `test_velocity_rule_reviews_after_enough_debits`, `test_daily_limit_counts_prior_debits` |
| RN20, US11 (desfecho block/review/approve) | `PaymentService._screen` | `tests/test_fraud.py::test_blocked_transfer_moves_no_money_and_is_failed`, `test_reviewed_transfer_is_held_without_moving_money`; `tests/test_api.py::test_large_transfer_is_blocked_by_fraud`, `test_medium_transfer_is_held_for_review_then_approved` |
| RN21 (resolução manual + re-validação de saldo) | `PaymentService.approve_review`, `reject_review` | `tests/test_fraud.py::test_approving_a_held_transfer_settles_it`, `test_rejecting_a_held_transfer_fails_it`, `test_approving_held_withdrawal_settles_against_settlement` |
| US01 (cadastro) | `AuthService.register` | `tests/test_api.py` (fluxo `_register_verified`, usado em todos os testes de integração) |
| US04 (depósito) | `PaymentService.create_deposit`, `confirm_deposit` | `tests/test_deposit.py::test_deposit_credits_account_only_after_confirmation`; `tests/test_api.py::test_full_deposit_and_transfer_flow` |
| US05 (saque) | `PaymentService.create_withdrawal` | `tests/test_withdrawal.py::test_withdrawal_debits_the_account`; `tests/test_api.py::test_deposit_then_withdraw_flow` |
| US06 (transferência) | `PaymentService.create_transfer` | `tests/test_transfers.py::test_successful_transfer_moves_money_between_accounts` |
| US08 (detalhe de transferência, autorização de recurso) | `app/api/routes/transfers.py::get_transfer` | Validado pela lógica de checagem `account.id not in (from_account_id, to_account_id)` (linha 83) |
| US10 (health check) | `app/main.py::health` | `tests/test_api.py::test_health` |

---

## Como manter este documento coerente

- Ao alterar uma regra de negócio no código, atualize a seção correspondente aqui **e** o cenário BDD relacionado.
- Ao adicionar um endpoint novo, crie a história de usuário, os critérios de aceite e o cenário Gherkin antes (ou junto) da implementação, quando possível.
- Este documento não substitui o [README.md](../README.md) (setup, stack, execução) nem o [docs/ARQUITETURA.md](ARQUITETURA.md) (arquitetura técnica, ADRs, diagramas de sequência) - ele é a camada de **requisitos e regras de negócio**, enquanto os demais cobrem as camadas **operacional** e **técnica/arquitetural**.
