## Resumo

<!-- O que mudou e por quê -->

## Revisão humana (obrigatória antes do merge)

> O código deste PR pode ter sido gerado ou assistido por IA. **Não mergear sem revisar o diff.**

### Checklist do revisor

- [ ] Li o diff completo no GitHub (aba **Files changed**)
- [ ] Entendi o que mudou e por que faz sentido no domínio
- [ ] Conferi se não há código morto, duplicação ou escopo fora do pedido
- [ ] Validei invariantes financeiros (ledger, idempotência, centavos `int`, locks)
- [ ] CI verde (Lint, Test, Sonar)
- [ ] Testes cobrem o comportamento novo ou alterado

### Depois de revisar

1. Marque os itens acima
2. Clique em **Approve** no PR (auto-revisão permitida neste repositório)
3. Faça o merge

## Checklist técnico

- [ ] Regras de negócio atualizadas em `docs/requisitos.md` (se aplicável)
- [ ] Testes adicionados ou atualizados em `tests/`
- [ ] Migrations Alembic incluídas (se schema mudou)

## Como validar

<!-- Comandos ou passos para testar localmente -->

```bash
pytest tests/ -v
ruff check app/ tests/
black --check app/ tests/
```
