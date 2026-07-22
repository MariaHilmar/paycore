## Resumo

<!-- O que mudou e por quê -->

## Checklist de revisão

- [ ] Regras de negócio atualizadas em `docs/requisitos.md` (se aplicável)
- [ ] Testes adicionados ou atualizados em `tests/`
- [ ] Invariantes financeiros preservados (ledger, idempotência, centavos int)
- [ ] Lint e formatação passam (`ruff check`, `black --check`)
- [ ] Migrations Alembic incluídas (se schema mudou)

## Como validar

<!-- Comandos ou passos para o revisor testar -->

```bash
pytest tests/ -v
ruff check app/ tests/
black --check app/ tests/
```
