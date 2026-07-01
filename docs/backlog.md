# Backlog

Move items from **Next** → **Done** in the same commit that ships the feature.

---

## Next

### 0. Instalação global sem venv manual
Poder correr `conductor` em qualquer diretório sem fazer `source .venv/bin/activate`.
Opções por ordem de esforço:
- **`pipx install -e .`** (zero código) — instala o conductor num env isolado e coloca-o
  no PATH global. Solução imediata se o `pyproject.toml` estiver correto.
- **Script wrapper em `~/.local/bin/conductor`** — aponta para o Python do venv do projeto.
- **Serviço de background** (`systemd --user` ou launchd) com socket/IPC — mais complexo,
  permite eliminar o cold-start do Python em cada invocação.
Avaliar `pipx` primeiro — pode resolver sem nenhuma alteração ao código.

### 1. Semantic stop conditions
Beyond the global iteration cap, the engine should stop early when it detects:
scope change, secrets/prod access, or a deadlock (repeated identical output).

### 3. B3 — interactive config cascade + dashboard triggers
Global → workspace → repo `.ai/` config override chain, editable from the
dashboard UI. Also: trigger/approve runs from the dashboard. This is where a
daemon + write access + secrets concerns enter (deferred until B1/B2 are
validated in real use).

### 4. Phased execution — planos grandes em contexto reduzido

Quando o planner gera um plano com muitas tasks (ex: 15 endpoints a documentar),
o implementer bloqueia porque o contexto excede a sua janela.

**Problema**: o implementer recebe o plano completo de uma vez e tenta fazer tudo
num único invocation. Modelos com janela menor (Qwen local, etc.) ficam bloqueados.

**Abordagem preferida — PHASE: markers no plano**:
O planner emite marcadores de fase no output:

```
PHASE 1: endpoints de autenticação (tasks 1-5)
...
PHASE 2: endpoints de dados (tasks 6-10)
...
```

O conductor executa uma fase de cada vez: passa ao implementer apenas a secção
`PHASE N` em vez do plano completo. Avança para a fase seguinte após review/validate.

**Alternativa — `conductor fork` manual por fase**:
Já disponível hoje: o utilizador faz `conductor fork "document auth endpoints (phase 1 of 3)"`
e repete. Funciona mas é manual.

**Ponto de implementação**:
- Planner role prompt: instruir a emitir `PHASE N:` quando o plano tem mais de X tasks
- Engine: detetar fases no output do planner, executar implementer por fase em loop
- CLI: mostrar progresso por fase (`phase 1/3 · implementing…`)
- `build_context`: passar só a secção da fase atual ao implementer

---

## Deferred / low priority

- **CLI tab completion** — Typer tem suporte nativo a shell completion (`add_completion=True`
  em `cli.py`). Completion dinâmica de workitem IDs e workspace names requer callbacks
  adicionais (`autocompletion=fn` nos argumentos). Mínimo viável: reactivar o autocomplete
  nativo para comandos e flags.

- **Terminal input no `refine`** — `typer.prompt()` não suporta readline (setas, ctrl+seta,
  histórico). Fix mínimo: activar `readline` stdlib antes do loop de perguntas em `cli.py`.
  Alternativa mais rica: `prompt_toolkit` (nova dependência).

- **AI para mensagens de commit** — actualmente `conductor accept` gera a mensagem
  mecanicamente (`feat: <título>\n\nWorkitem: <id>`). Avaliar chamar um provider leve
  com o `git diff --staged` para uma mensagem mais descritiva. Manter o fallback mecânico
  se o provider falhar. Avaliar se o ganho justifica a latência extra no `accept`.

- **Session-resume for `refine` loop** — CLIs like `claude` (`--resume <id>`)
  and opencode support session continuation, which would avoid resending the
  full context (role prompt + instructions + goal) on every clarification round.
  Not critical: refine is typically 1–3 rounds with a small transcript.
  Blocked on settling on a single preferred CLI (e.g. opencode with all models
  configured). See `TODO` in `src/conductor/providers/cli_one_shot.py`.

---

## Done

- Live progress indicator — spinner + elapsed timer in `execute`, `refine`, workspace execute
- Config wizard + global defaults — `conductor config` / `conductor config --global` + cascade merge
- Workspace execute — `conductor execute/accept -w <workspace>` with two-phase cross-project flow
- Sessions/sandbox — git worktree isolation for `execute` / `accept` / `reopen`
- MVP 1–2: full `define → refine → approve → execute` loop with review/fix gate
- Providers: `cli_one_shot` (claude, qwen, codex) / `api` / `ollama` / `dry_run`
- Refiner gate robustness: QUESTIONS:/CONTRACT: markers, YAML preprocessor
- Context/token strategy: dedup by role + 8 k char cap (`_prior_outputs`)
- Visibility B1+B2: workspace registry + read-only `conductor dashboard`
- Cross-project workitems: `conductor define/refine/approve/status -w <workspace>`
- `conductor reopen "<reason>"` — reset + `reopen.md` injected as planner context
- `conductor accept` — `git add -A` + commit with goal title + `--push` flag
