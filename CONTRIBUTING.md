# Contribuindo

## Fluxo de trabalho

1. Crie uma branch a partir de `main` (`feat/`, `fix/`, `docs/`).
2. Abra um **pull request** - push direto em `main` esta bloqueado.
3. Aguarde o **CI** (Lint + Test) ficar verde.
4. Solicite **revisao** (humana ou Bugbot/Cursor nas regras de `.cursor/BUGBOT.md`).
5. Apos aprovacao, faca merge do PR.

## Validar localmente

```powershell
.\scripts\test.ps1
```

## Commits

Use [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`, `chore:`, `test:`.
