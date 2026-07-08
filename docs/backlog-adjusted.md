Claro — aqui está o **backlog completo consolidado** do `workitem-conductor`, já considerando tudo que decidimos:

* workitems fora do projeto;
* `.ai/` do projeto apenas com configuração versionável;
* runtime centralizado;
* sem necessidade de compatibilidade com workitems antigos;
* foco inicial em contexto, métricas, inspect e estrutura para futuro daemon/API;
* OpenCode como worker, não como substituto do conductor;
* inspiração Sakana/TRINITY apenas onde faz sentido.

# Backlog consolidado — workitem-conductor

## Princípio arquitetural

O projeto passa a separar claramente:

```text
project/.ai/
  configuração versionável do projeto

~/.config/conductor/
  configuração global do conductor

~/.local/share/conductor/
  runtime centralizado: workitems, runs, artifacts, worktrees, métricas

~/.cache/conductor/
  cache temporário
```

A frase-guia:

> O projeto define **como deve ser trabalhado**.
> O conductor guarda **o que foi trabalhado, como foi executado e com que resultado**.

---

# M1 — Central Runtime Foundation

## Objetivo

Criar a base de paths e runtime centralizado.

## Itens

### 1. Criar `ConductorHome`

Resolver os diretórios padrão:

```text
~/.config/conductor/
~/.local/share/conductor/
~/.cache/conductor/
```

### 2. Separar config, data e cache

Criar helpers internos:

```python
config_home()
data_home()
cache_home()
```

Nenhum módulo novo deve montar esses paths manualmente.

### 3. Atualizar `conductor doctor`

Mostrar:

```text
Config home: ~/.config/conductor
Data home:   ~/.local/share/conductor
Cache home:  ~/.cache/conductor
```

### 4. Definir layout central

```text
~/.local/share/conductor/
  library/
  workspaces/
  index/
```

---

# M2 — Config and Library Cascade

## Objetivo

O conductor nasce com ferramentas próprias, e cada projeto sobrescreve apenas o que precisar.

## Itens

### 5. Criar library global

```text
~/.local/share/conductor/library/
  roles/
  flows/
  strategies/
  commands/
  templates/
```

### 6. Mover defaults para library

Primeira versão:

```text
roles/
  refiner.md
  planner.md
  implementer.md
  reviewer.md
  summarizer.md

flows/
  simple-change.yml
  workspace-change.yml

strategies/
  simple-change.yml
  bugfix.yml
```

### 7. Implementar resolução em cascata

Ordem:

```text
built-in
→ global library
→ workspace
→ project .ai/
→ workitem override
```

### 8. Reduzir `conductor init`

Depois do `init`, o projeto deve ter apenas:

```text
.ai/
  repo.yml
  instructions.md
```

Opcionalmente, no futuro:

```text
.ai/
  roles/        # overrides
  flows/        # overrides
  strategies/   # overrides
  commands/     # overrides
```

### 9. Mostrar origem das configs

Algo como:

```bash
conductor doctor --config
```

Exemplo:

```text
role planner: global library
role reviewer: project override
flow simple-change: global library
strategy bugfix: global library
```

---

# M3 — Clean Break: Central Workitems and Worktrees

## Objetivo

Remover runtime de dentro do projeto.

## Decisão

Não suportar workitems antigos.

Sem migração.
Sem fallback.
Sem `.ai/workitems`.
Sem `.ai/worktrees`.
Sem `.ai/active_workitem.txt`.

## Itens

### 10. Remover expectativa de `.ai/workitems`

Novo destino:

```text
~/.local/share/conductor/workspaces/<workspace>/workitems/
```

### 11. Remover expectativa de `.ai/worktrees`

Novo destino:

```text
~/.local/share/conductor/workspaces/<workspace>/worktrees/
```

### 12. Workspace implícito para projeto único

Todo projeto passa a ser um workspace, mesmo que tenha só um repo:

```yaml
name: workitem-conductor
projects:
  - name: main
    path: /home/tiago/projects/workitem-conductor
```

### 13. Criar workitem no storage central

Estrutura mínima:

```text
~/.local/share/conductor/workspaces/<workspace>/
  active_workitem.txt
  workspace.yml
  workitems/
    <workitem-id>/
      goal.yml
      state.yml
      memory.yml
      outputs/
      artifacts/
```

### 14. Guardar `project_refs`

Cada workitem deve saber quais projetos toca:

```yaml
project_refs:
  main:
    path: /home/tiago/projects/workitem-conductor
    ai_profile: /home/tiago/projects/workitem-conductor/.ai
    source_branch: main
    target_branch: main
```

### 15. Ajustar `define`, `status`, `approve`, `refine`

Todos devem operar sobre o storage central.

### 16. Ajustar `execute`

O `execute` deve:

```text
- ler o workitem central
- resolver project_refs
- criar worktree central
- executar provider no worktree correto
- gravar outputs no workitem central
```

### 17. Novo layout de worktrees

Para projeto único:

```text
~/.local/share/conductor/workspaces/workitem-conductor/
  worktrees/
    wi-123/
      main/
```

Para workspace multi-repo:

```text
worktrees/
  wi-123/
    backend/
    frontend/
    docs/
```

---

# M4 — Runs, Metrics and Inspect

## Objetivo

Cada execução precisa ser auditável, mensurável e fácil de inspecionar.

## Decisões de implementação (2026-07-08)

Ao implementar M4 sobre o código real, duas decisões deliberadas divergem do
texto original abaixo:

- **Item 20 (mover prompts/outputs para `runs/<id>/steps/`) — não foi feito
  fisicamente.** `outputs/NN-role.{prompt,output}.md` continua exatamente como
  estava (plano, monotónico) porque `core/context.py::_prior_outputs()` já faz
  glob a esse diretório para montar o "prior step outputs" injetado no prompt
  seguinte; mover os ficheiros obrigaria a reescrever essa lógica para varrer
  múltiplos diretórios de run, sem ganho funcional. Em vez disso, `run.yml` é
  um **manifesto**: cada `StepRecord` tem `index`, `prompt_path` e
  `output_path` (relativos ao diretório do workitem) que apontam para os
  ficheiros existentes em `outputs/`, preservando a auditabilidade pedida sem
  duplicar ou realocar artefactos.
- **Escopo limitado ao `Engine` single-repo.** `WorkspaceEngine` (execução
  `-w`, cross-project) não grava `run.yml`/`metrics.yml` nesta fase — mesmo
  padrão adotado no M3 (`WorkspacePaths` ficou de fora do primeiro corte e foi
  integrado depois, a pedido). Fast-follow explícito, não um esquecimento.

## Itens

### 18. Criar conceito de `run`

Cada `conductor execute` cria um novo run:

```text
workitems/
  wi-123/
    runs/
      run-001/
      run-002/
```

### 19. Criar `run.yml`

Exemplo:

```yaml
run_id: run-001
workitem_id: wi-123
workspace: workitem-conductor
started_at: ...
finished_at: ...
status: completed
strategy: simple-change
source: execute
reopen_number: 0
steps:
  - role: planner
    provider: qwen_api
    ok: true
    duration_sec: 31
    prompt_chars: 12000
    output_chars: 4000
```

### 20. Guardar prompts e outputs por run

```text
runs/
  run-001/
    steps/
      01-planner.prompt.md
      01-planner.output.md
      02-implementer.prompt.md
      02-implementer.output.md
      03-reviewer.prompt.md
      03-reviewer.output.md
```

### 21. Criar `metrics.yml`

Exemplo:

```yaml
context:
  total_prompt_chars: 54000
  max_step_prompt_chars: 21000
  compressed: false

git:
  files_changed: 4
  insertions: 120
  deletions: 30

loop:
  fix_iterations: 1
  reopen_number: 2

providers:
  planner: qwen_api
  implementer: opencode_cli
  reviewer: claude_cli
```

### 22. Capturar métricas mínimas

```text
- duração por step
- provider usado
- prompt chars
- output chars
- resultado
- fix iterations
- reopen number
- arquivos alterados
- insertions/deletions
```

### 23. Criar `conductor inspect`

Primeira versão:

```bash
conductor inspect --active
conductor inspect wi-123
conductor inspect wi-123 --runs
conductor inspect wi-123 --context
```

Mostrar:

```text
goal
state
workspace
projects
runs
steps
providers
metrics
artifacts
diff
open issues
next action
```

---

# M5 — Context Memory and Reopen Compaction

## Objetivo

Resolver o problema de explosão de contexto em workitems medianos com vários reopens.

## Estado (2026-07-08): implementado — itens 24-30 concluídos

Ao contrário do M3/M4 (que fizeram cortes propositadamente mais cautelosos),
aqui foi feito o corte completo já nesta fase, por decisão explícita:

- **Item 20 (M4) não foi revisitado** — `outputs/NN-role.*.md` continua flat,
  `run.yml` continua a ser manifesto, não cópia física.
- **`build_context()` deixou de incluir raw outputs por omissão** —
  `include_raw_outputs: false` é agora o default; memory.yml passa a ser a
  fonte de contexto por omissão (`include_memory`, `include_last_diff`,
  `include_last_review` todos `true` por omissão). Raw outputs continuam
  disponíveis como opt-in explícito em `repo.yml` (`context.include_raw_outputs: true`)
  para repos que ainda não confiem no summarizer.
- **`conductor reopen` também dispara o summarizer** — resolve um provider
  como `execute`/`refine` já faziam, envolto em try/except para nunca
  bloquear o reopen (o reset de estado tem de continuar fiável mesmo com
  summarizer mal configurado ou provider a falhar).
- **Fora de escopo, mesmo padrão do M3/M4**: `WorkspaceEngine`/`conductor
  reopen -w` não têm summarizer nem contexto curado nesta fase.

## Itens

### 24. Criar `memory.yml`

```yaml
current_summary: >
  Estado atual do workitem.

decisions:
  - at: ...
    by: human
    decision: Não alterar contrato público da API.

open_issues:
  - Corrigir teste X.
  - Validar migration Y.

resolved_issues:
  - O erro inicial de path foi corrigido.

validation_status:
  last_tests:
    - pytest tests/test_x.py
  failing:
    - test_policy_order
```

### 25. Criar `current_summary.md`

```text
context/
  current_summary.md
```

Resumo humano/LLM do estado atual.

### 26. Criar role `summarizer`

O summarizer atualiza:

```text
memory.yml
context/current_summary.md
```

Depois de:

```text
- execute
- reopen
- review com changes_requested
- run bloqueado
```

### 27. Criar `context compiler`

Novo módulo:

```text
core/context_compiler.py
```

Responsável por gerar contexto por role.

Entrada:

```text
goal.yml
state.yml
memory.yml
último run
último review
último diff
project instructions
```

Saída:

```text
context/role-packs/
  planner.md
  implementer.md
  reviewer.md
  summarizer.md
```

### 28. Substituir montagem direta de contexto

O `build_context` deve deixar de juntar outputs antigos diretamente.

Em vez disso:

```text
raw artifacts completos ficam guardados
context pack recebe apenas memória curada
```

### 29. Context budget explícito

Em config ou strategy:

```yaml
context:
  max_prompt_chars: 64000
  include_raw_outputs: false
  include_memory: true
  include_last_diff: true
  include_last_review: true
```

### 30. Reopen compaction

Em cada `reopen`, o conductor deve:

```text
- registrar razão do reopen
- atualizar memory.yml
- compactar histórico relevante
- evitar reenviar todos os outputs antigos
```

---

# M6 — Safety Stop Conditions

## Objetivo

Tornar a execução mais segura antes de aumentar autonomia.

## Itens

### 31. Semantic stop conditions

Detectar:

```text
- alteração fora de escopo
- acesso a secrets
- comandos perigosos
- tentativa de produção
- loop repetitivo
- reviewer/implementer deadlock
```

### 32. Gravar stop reason estruturado

```yaml
status: needs_human
stop_reason:
  type: scope_change
  message: ...
  evidence:
    - ...
```

### 33. Diferenciar `blocked` de `needs_human`

```text
blocked:
  erro técnico ou provider falhou

needs_human:
  decisão, risco ou desvio semântico
```

### 34. Integrar stop conditions no final report e inspect

`inspect` deve mostrar claramente:

```text
why stopped
what evidence
what next action
```

---

# M7 — Strategy Model and Strategy Selector

## Objetivo

Sair de flow fixo para estratégias selecionáveis.

## Itens

### 35. Criar modelo `Strategy`

Exemplo:

```yaml
name: simple-change
flow: simple-change

roles:
  planner:
    provider: default_planner
  implementer:
    provider: default_implementer
  reviewer:
    provider: default_reviewer

context:
  max_prompt_chars: 64000

budgets:
  max_fix_iterations: 3
  max_reopens_before_summary_required: 1
```

### 36. Estratégias iniciais

```text
simple-change
bugfix
context-heavy-change
phased-documentation
cross-project-change
tpa-claim-flow
```

### 37. Strategy selector por regras

Inicialmente simples:

```text
se target_projects > 1 → cross-project-change
se reopen_count >= 2 → context-heavy-change
se acceptance_criteria contém docs → phased-documentation
senão → simple-change
```

### 38. Strategy no `goal.yml` ou `state.yml`

O workitem deve registrar qual strategy foi usada.

### 39. Strategy snapshot por run

Cada run deve guardar a versão/hash da strategy usada.

---

# M8 — Phased Execution

## Objetivo

Evitar planos grandes demais e reduzir contexto por fase.

## Itens

### 40. Planner emite fases

Formato inicial:

```text
PHASE 1: ...
PHASE 2: ...
PHASE 3: ...
```

### 41. Parser de fases

Extrair fases do output do planner.

### 42. Engine executa fase por fase

Cada fase vira um step group ou sub-run.

```text
phase 1
  implementer
  reviewer

phase 2
  implementer
  reviewer
```

### 43. Contexto por fase

O implementer recebe apenas:

```text
goal
memory
fase atual
decisões relevantes
último review da fase
```

Não recebe o plano completo se não for necessário.

### 44. Review por fase

Só avança para próxima fase se a atual for aprovada.

---

# M9 — Structured Roles and Verifier/Integrator

## Objetivo

Tornar os outputs dos roles parseáveis e úteis para routing.

## Itens

### 45. Structured output para reviewer

Exemplo:

```yaml
verdict: changes_requested
confidence: 0.78
blocking_issues:
  - ...
non_blocking_issues:
  - ...
suggested_next_role: implementer
```

### 46. Structured output para planner

```yaml
branch: feat/...
phases:
  - name: ...
    goal: ...
    files_likely_touched:
      - ...
risk_level: medium
```

### 47. Criar role `verifier`

Responsável por validação objetiva:

```text
- rodar/indicar testes
- verificar critérios de aceitação
- confirmar se output cumpre contrato
```

### 48. Criar role `integrator`

Especialmente para workspace multi-repo:

```text
- backend mudou API
- frontend atualizado
- docs atualizadas
- contratos entre projetos coerentes
```

### 49. Criar role `decomposer`

Quebra workitem grande em fases/subtasks.

---

# M10 — OpenCode Provider and Permissions

## Objetivo

Usar OpenCode como worker especializado, sem transformar o conductor em OpenCode.

## Itens

### 50. Criar provider `opencode`

Não apenas `cli_one_shot`.

Exemplo:

```yaml
providers:
  opencode_build:
    type: opencode
    agent: build
    model: qwen-coder
    mode: build
    session: true
```

### 51. Suportar agent/mode/model/session

```text
agent
mode
model
session resume
working directory
streaming
```

### 52. Mapear roles para agentes OpenCode

```yaml
roles:
  implementer:
    provider: opencode_build
  reviewer:
    provider: claude_cli
```

### 53. Permissions por role

Inspirado em `ask/allow/deny`:

```yaml
roles:
  reviewer:
    permissions:
      edit: deny
      bash: deny

  implementer:
    permissions:
      edit: allow
      bash:
        "pytest*": allow
        "npm test*": allow
        "rm -rf *": deny
```

### 54. Enforcement inicial simples

Primeiro por prompt/config.

Depois, se possível, enforcement real no provider.

---

# M11 — SQLite Index, Metrics CLI and Dashboard

## Objetivo

Permitir análise histórica e dashboard eficiente.

## Decisão

SQLite não é fonte da verdade inicialmente.

```text
YAML/Markdown = fonte humana e auditável
SQLite = índice derivado para consultas e métricas
```

## Itens

### 55. Criar index SQLite

```text
~/.local/share/conductor/index/conductor.sqlite
```

### 56. Criar `conductor index rebuild`

Reconstrói o DB a partir dos YAMLs.

### 57. Tabelas iniciais

```text
workspaces
projects
workitems
runs
steps
providers
metrics
reopens
artifacts
```

### 58. Criar `conductor metrics`

Comandos:

```bash
conductor metrics providers
conductor metrics strategies
conductor metrics reopens
conductor metrics context
conductor metrics workspaces
```

### 59. Dashboard passa a usar index

Em vez de varrer todos os ficheiros a cada refresh.

### 60. Dashboard write-mode local

Depois do storage/index:

```text
- aprovar goal
- executar workitem
- reabrir
- aceitar
- ver artifacts
```

---

# M12 — Server Readiness

## Objetivo

Preparar caminho para VM, daemon ou API futura.

## Itens

### 61. Storage abstraction

Criar abstração:

```python
StorageBackend
LocalFileStorage
```

Futuro:

```python
ApiStorage
RemoteStorage
```

### 62. Evitar dependência direta em `Path.cwd()`

Separar:

```text
current shell cwd
project root
workspace root
execution cwd
storage root
```

### 63. Criar `ProjectRegistry`

Centralizar projetos conhecidos.

### 64. Preparar `RunQueue`

Mesmo que inicialmente síncrona.

Futuro:

```text
queued
running
completed
failed
needs_human
```

### 65. Preparar `conductor daemon`

Não implementar já, mas evitar decisões que bloqueiem.

### 66. Pensar modo VM/API

Layout futuro:

```text
/etc/conductor/
  config.yml

/var/lib/conductor/
  library/
  workspaces/
  projects/
  index/
```

---

# M13 — Evaluation Harness and Adaptive Routing

## Objetivo

Começar a aprender empiricamente quais estratégias/providers funcionam melhor.

## Itens

### 67. Evaluation harness

Rodar exemplos reais:

```text
examples/
  bugfix-small
  cross-project-contract
  context-heavy-reopen
```

### 68. Comparar estratégias

Medir:

```text
success
duration
provider calls
fix iterations
reopens
context size
files changed
```

### 69. Provider scoring

Responder perguntas como:

```text
Qwen foi melhor para implementer?
Claude foi melhor para reviewer?
OpenCode reduziu loops?
Qual provider explodiu contexto?
```

### 70. Prompt evolution

Versionar prompts:

```text
planner v1
planner v2
reviewer v1
reviewer v2
```

Medir resultados.

### 71. Adaptive routing

Futuro:

```text
conductor decide próximo role com base em evidência
não apenas flow fixo
```

---

# Ordem prática para começar agora

Eu começaria pelos primeiros 7 dias assim.

## Dia 1 — ConductorHome

```text
- Criar config_home/data_home/cache_home
- Atualizar doctor
- Definir layout central
```

## Dia 2 — Library global

```text
- Criar library/
- Mover roles/flows defaults
- Implementar cascade
- Reduzir init
```

## Dia 3 — Workspace implícito

```text
- Todo projeto vira workspace de 1 projeto
- Criar workspace.yml central
- Criar active_workitem central
```

## Dia 4 — Central workitems/worktrees

```text
- define cria workitem central
- status/approve/refine usam central
- execute cria worktree central
```

## Dia 5 — Runs e metrics v1

```text
- Criar run-id
- Criar run.yml
- Criar metrics.yml
- Guardar prompts/outputs por run
```

## Dia 6 — Inspect v1

```text
- conductor inspect --active
- Mostrar goal/state/runs/metrics/artifacts/diff
```

## Dia 7 — Memory/context v1

```text
- Criar memory.yml
- Criar current_summary.md
- Injetar memory no contexto
- Reduzir dependência de outputs antigos
```

---

# O que não fazer agora

Deixar para depois:

```text
OpenCode provider dedicado
SQLite
dashboard write-mode
daemon/API
adaptive routing
prompt evolution
permissions complexas
session resume
```

Porque tudo isso depende primeiro de:

```text
runtime central
runs
metrics
inspect
memory/context compiler
```

---

# Versão curta dos milestones

```text
M1  — Central Runtime Foundation
M2  — Config and Library Cascade
M3  — Clean Break: Central Workitems and Worktrees
M4  — Runs, Metrics and Inspect
M5  — Context Memory and Reopen Compaction
M6  — Safety Stop Conditions
M7  — Strategy Model and Strategy Selector
M8  — Phased Execution
M9  — Structured Roles and Verifier/Integrator
M10 — OpenCode Provider and Permissions
M11 — SQLite Index, Metrics CLI and Dashboard
M12 — Server Readiness
M13 — Evaluation Harness and Adaptive Routing
```

A primeira fatia realmente valiosa é:

```text
ConductorHome
+ library cascade
+ central workitem storage
+ central worktrees
+ run.yml/metrics.yml
+ inspect
+ memory.yml
```

Isso transforma o projeto de um CLI que coordena agentes dentro de um repo para um **runtime centralizado de workitems**, que é a base certa para tudo que queremos depois.
