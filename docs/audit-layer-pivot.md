# Pivot: de orquestrador de coding roles para camada de registo/auditoria de planos

**Data:** 2026-07-17
**Branch:** `docs/audit-layer-pivot` (a partir de `main`, pós-merge de `feat/centralize-workitems`)
**Estado:** proposta para validação externa — nada deste documento foi implementado ainda.

Este documento existe para ser validado fora desta conversa antes de qualquer código ser escrito. Regista o raciocínio completo, não só a conclusão, para que uma leitura externa consiga discordar de um passo específico sem ter de reconstruir todo o histórico.

---

## 1. Como chegámos aqui

### 1.1 O problema original (resolvido, não pelo `workitem-conductor`)

O projeto nasceu para resolver uma dor operacional concreta: múltiplas assinaturas de AI (Codex, Claude, Kimi, etc.), cada uma com o seu CLI, e a fricção de copiar prompt/output de um lado para o outro à mão. A ideia original era um orquestrador que chamasse o CLI certo por papel (`planner`/`implementer`/`reviewer`/...).

**Esse problema já não existe.** O OpenCode (`~/.config/opencode/`) passou a resolver o roteamento multi-modelo nativamente — providers configuráveis, agentes com modelo/permissões próprios por papel (`implementer.md`, `reviewer.md`, `tester.md`, `committer.md`, orquestrados por `workitem-conductor.md`), tudo já em uso diário real. Confirmado explicitamente: "já morreu, uso o opencode pra tudo hoje em dia."

### 1.2 O que ficou depois de a dor original desaparecer

Duas motivações sobreviveram à morte do problema original, ambas descobertas *durante* esta conversa, não desenhadas de propósito desde o início:

1. **Auditabilidade** — inspirado no AI Act da UE (com a ressalva honesta de que um assistente de coding provavelmente não é "high-risk" pela letra da lei, mas o princípio de decisão rastreável/replayável aplica-se como boa prática, e generaliza a outros domínios: foi dado como exemplo um agente interno de processamento de sinistros de seguros).
2. **"Aprender a orquestrar"** (inspiração Sakana/Trinity, de um meetup anterior) — hoje usa-se GPT-5.5 como orquestrador no OpenCode por ser a aposta segura, sem dados que confirmem se compensa o custo face a um modelo mais barato. Isto **depende** do ponto 1: sem registo estruturado de que modelo orquestrou, quanto custou, quantos loop-backs foram precisos, não há dados para decidir.

### 1.3 A descoberta que resolveu "como capturar"

Investigação a `~/.local/share/opencode/opencode.db` (SQLite, 3.6GB) confirmou que o OpenCode **já regista tudo o que estas duas motivações precisam**, nativamente, sem qualquer integração nova:

- Tabela `session`: uma linha por invocação de agente — `agent` (nome do papel, ex. `"implementer"`, `"reviewer"`, `"workitem-conductor"`), `model` (id+provider), `cost`, `tokens_input/output/reasoning/cache`, `parent_id` (liga subagentes à sessão que os chamou), `directory`, timestamps.
- Tabelas `message`/`part`: transcript completo — texto final (incluindo veredictos como `APPROVED`/`CHANGES_REQUIRED`), chamadas de bash com comando+output, reasoning traces.
- Confirmado em dados reais: uma sessão-mãe `agent: workitem-conductor`, `model: gpt-5.5`, com filhas `implementer` (por vezes `claude-sonnet-5`, por vezes `qwen3-coder-plus`) e `reviewer` (`gpt-5.5`).

Conclusão: **não é preciso construir nenhuma camada de captura.** Construir um wrapper que invoca o `opencode` como subprocesso e apanha stdout/exit-code/diff (a proposta inicial de outra análise, feita sem conhecer este ficheiro) seria estritamente pior — perderia a atribuição por papel, custo/tokens, e reasoning traces que o `opencode.db` já dá de graça. E reintroduziria fricção: obrigar a invocar tudo através do `conductor run --executor opencode ...` é o mesmo tipo de fricção que matou a motivação original (1.1).

### 1.4 A peça que faltava: identidade estável do "workitem"

O `opencode.db` não tem noção de "isto é a mesma unidade de trabalho de negócio ao longo de vários dias/sessões" — sessões são só threads de conversa. Essa identidade já existe informalmente na prática do utilizador: ficheiros `execution_plans/*.md` por repositório (ex. `.ai/execution_plans/2026-07-15_inline-step-delegation-and-field-clear-affordances.md`), escritos numa conversa com o Claude, depois entregues ao comando `/implement-plan` do OpenCode (que injeta literalmente `"Read the implementation plan from @$ARGUMENTS"` no prompt).

**Verificado em dados reais**: o caminho do ficheiro do plano aparece verbatim na primeira mensagem da sessão-mãe no `opencode.db` (`LIKE '%execution_plans%'` encontrou-o diretamente). A correlação plano↔sessões custa uma query, sem integração nova.

### 1.5 O caso real que expôs os limites do formato ad-hoc

Uma sprint real com **20 execution plans** espalhados por 5+ repositórios (TPA, JC, hub, care BE, care FE, selfcare), com dependências cruzadas ("depende do #17"), uma tarefa embutida sem ficheiro próprio dentro de outro plano ("Task 0"), e uma equipa externa a executar um plano sem acesso cross-repo (exigindo que esse plano fosse autossuficiente) — tudo isto tracked à mão numa tabela markdown mantida manualmente. Isto é dor real, presente, não hipotética — mais urgente do que a correlação de auditoria em si.

### 1.6 Isto já foi tentado antes: lições do V4/V5 (`habit-ai-orchestrator`)

`workitem-conductor` nasceu literalmente dentro de `habit-ai-orchestrator` — `docs/vision.md` e `docs/local-agent-brief.md` desse repositório têm o mesmo tagline ("Define the goal. Let agents do the loop. Review the result.") e a mesma frase de posicionamento ("It is not a coding agent. It coordinates coding agents."). Esse repositório correu em produção real durante ~6 semanas (maio–meados de junho de 2026, 90+ workitems reais, features do TPA), evoluiu de um modelo V4 pesado para um V5 deliberadamente simplificado ("use the smallest workflow that preserves safety"), e parou de ser usado a 2026-06-15 — sem uma razão registada nos commits.

Confirmado pelo utilizador e verificado nos ficheiros reais:

1. **Bureaucracia desproporcional ao risco.** O V5 já tentava resolver isto com perfis `quick`/`standard`/`governed`, mas o próprio scorecard de avaliação (`docs/v5-evaluation-scorecard.md`) tem um campo explícito **"Too bureaucratic: yes | no"** — o risco já era antecipado, e a perceção do utilizador ("para um pequeno bugfix o trabalho era muito grande") sugere que se materializou mesmo depois da simplificação.
2. **Cada agente a escrever o seu próprio ficheiro de artefacto não funcionou — confirmado em dados reais.** Comparando dois workitems reais: `2026-06-11_tpaclaims-claim-action-links/` tem `task.md`/`plan.md`/`state.json`/`result.md`; `2026-05-21_tpaclaims-payment-worklist-paid-status-contract/` tem `meta.json` (não `state.json`), `request.md`, `backend_instructions.md`, `frontend_instructions.md` — nomes de ficheiro diferentes para o mesmo tipo de informação, **apesar de existir um `workitem-state.schema.json` formal**. O esquema não impediu o desvio porque nada obrigava um agente a produzir exatamente esses ficheiros com esses nomes — daí a intuição (já do próprio utilizador, à época) de mudar para algo mais estruturado, que acabou por levar ao YAML no `workitem-conductor`.
3. **O bloco `## Workflow evaluation`** (visto embutido em `result.md` de um workitem real: `prompt_count_estimate`, `human_correction_count`, `rework_loops`, `workflow_overhead`, `confidence`, etc.) é, na prática, o mesmo objetivo da motivação "aprender a orquestrar" de hoje — só que auto-reportado por um agente em prosa estruturada, sujeito ao mesmo risco de desvio do ponto 2, em vez de derivado de dados que já existem de forma consistente.

**O que isto valida no desenho de hoje, e o que corrige deliberadamente:**

| V4/V5 | Desenho de hoje | Porquê |
|---|---|---|
| Vários ficheiros por workitem, escritos à mão por agentes diferentes (`task.md`/`plan.md`/`result.md`/`review.md`/`test.md`/...) | **Um único documento em prosa** (`execution_plan.md`) por unidade de trabalho, com frontmatter mínimo | Elimina a superfície onde o desvio de formato aconteceu — não há "vários ficheiros para manter consistentes entre si" |
| `## Workflow evaluation` auto-reportado por um agente | Métricas (custo, tokens, modelo, nº de sessões/loop-backs) **lidas do `opencode.db`**, não escritas por um agente | O dado já existe, gerado pela própria plataforma, não depende de disciplina de um prompt para ser preenchido corretamente |
| Perfis `quick`/`standard`/`governed` como conceito explícito, com artefactos obrigatórios por perfil | Frontmatter quase todo opcional (só `id`/`status` obrigatórios); um plano "quick" é só um plano sem `depends_on`/`sprint`/`owner_team` preenchidos — o perfil emerge dos campos usados, não é um modo à parte a manter | Evita reintroduzir a mesma escada de bureaucracia que o V5 já tentou achatar e (aparentemente) não achatou o suficiente |
| Orquestrador como fallback, não passo obrigatório | Mesmo princípio, já adotado — o `workitem-conductor` (ferramenta) nunca executa nada, só regista/valida | Validado, sem alteração necessária |
| `repositories: [{repo, role: primary\|changed\|verification_only\|dependency\|orchestration}]` no schema | Ainda por decidir se `depends_on`/`related` (§5) chega, ou se vale a pena um campo `repos_affected` com papel por repo, mais rico do que só `repo:` | Ideia genuinamente boa do V5 que o desenho de hoje ainda não captura — ver §7 |

---

## 2. Decisão: control plane, não execution plane

`OpenCode = execution plane` (planeia, edita, revê, testa, usa ferramentas).
`workitem-conductor = control plane + audit plane` (regista intenção, dependências, evidência, decisão humana).

O critério de sobrevivência de qualquer peça do projeto: **só existe se responder a uma pergunta que os logs soltos do OpenCode, sozinhos, não respondem bem.**

```text
Qual era o objetivo aprovado?
Que planos dependem de quê, e nessa ordem?
Este plano está pronto para ser entregue a outra equipa sem contexto extra?
Que sessões/custo/modelo executaram este plano?
Que decisão humana aceitou ou reabriu isto?
```

### 2.1 A simplificação final: nem a conversa de criação do plano fica no conductor

Ponto de viragem desta conversa: um comando `/create-plan` no OpenCode (irmão do `/implement-plan` já existente) pode conduzir a conversa "transformar frase genérica num plano" — exatamente onde já se vive o dia todo — em vez de o `workitem-conductor` manter o seu próprio ciclo `define`/`refine`/`approve` a chamar modelos.

Isto significa que o `workitem-conductor` final **não faz nenhuma chamada a um LLM**. Zero. É scanner de ficheiros + leitor de SQLite + validador + gerador de relatórios.

---

## 3. O que morre

**Decisão explícita: apagar, não congelar.** Uma revisão externa (GPT) sugeriu manter este código escondido atrás de um `conductor legacy execute` por 2-3 semanas antes de remover, para evitar um "refactor destrutivo". Essa cautela faz sentido quando há equipa/produção a depender do código — não é o caso aqui: utilizador único, nunca chegou a usar M5-M9 em trabalho real, e o git preserva tudo de qualquer forma (nada se perde — está a um `git log`/checkout de distância). Manter uma via `legacy` viva é só superfície morta (imports, testes a passar por código que ninguém usa) sem benefício real. Corte total, já, nesta pivot.

Tudo o que competia com o OpenCode em orquestração/execução, e tudo o que existia só para servir essa orquestração:

- `core/engine.py` (`Engine`), `core/workspace_engine.py` (`WorkspaceEngine`)
- `flows/models.py` (`Flow`, `FlowStep`, `phase_flow`)
- `strategies/` (`Strategy`, seletor)
- `core/review.py`, `core/verify.py`, `core/planner_output.py` — parsing de output estruturado de roles que deixam de existir
- `core/stop_conditions.py` — deteção de `STOP:` marker num output que o conductor deixa de gerar
- `core/runs.py` (`RunRecord`/`StepRecord`/`MetricsRecord`) — modelo de auditoria desenhado à volta de o `Engine` executar passos; substituído pela leitura direta do `opencode.db`
- `core/context.py` — construção de prompt para roles que deixam de ser chamados pelo conductor
- `core/summarize.py` + `memory.yml`/`MemoryRecord` — compactação de uma conversa longa própria que deixa de existir (a "conversa" passa a ser uma sessão OpenCode, já registada no `opencode.db`)
- `core/worktree.py` — criação de worktrees; git continua a ser gerido pelo utilizador/OpenCode diretamente
- `workitems/manager.py`'s `define`/`refine`/`approve`/`reopen`/`accept` e todo o ciclo de vida de `WorkitemState` — a conversa de criação do plano muda de casa para o `/create-plan` do OpenCode
- `scaffold.py` — scaffolding de flows/roles/strategies
- `providers/` (`cli_one_shot`/`api`/`ollama`/`dry_run`) — o conductor deixa de chamar qualquer provider

### O que sobrevive/é reaproveitado

- Padrão de modelos pydantic com `to_yaml`/`from_yaml` (usado noutro contexto: o modelo do frontmatter do plano)
- `core/yaml_utils.py` — parsing de blocos YAML/frontmatter, diretamente reaproveitável
- Conceito de registo de projetos/workspaces (`~/.config/conductor/workspaces.yml`) — passa a ser "que repositórios têm `.ai/execution_plans/` para varrer", não "que repositórios fazem parte de um workitem cross-projeto"
- `home.py` (`config_home`/`data_home`/`cache_home`) — continua a fazer sentido para uma cache local do índice de planos (ver §6)

---

## 4. Desenho novo

### 4.1 No OpenCode: `/create-plan` chama um agente próprio, isolado do `workitem-conductor` existente

`/create-plan` (`~/.config/opencode/commands/create-plan.md`) não reaproveita o agente `workitem-conductor` (o que hoje orquestra implementer/reviewer/tester/committer, sempre restrito ao `cwd`). Chama um **agente novo e dedicado**, ex. `~/.config/opencode/agents/plan-writer.md`, com um perfil de permissões próprio — o `workitem-conductor` e os seus subagentes ficam exatamente como estão hoje, sem qualquer alteração.

```yaml
---
description: Conducts a cross-repo planning conversation and writes execution_plans/*.md, with a distinct (broader) permission profile from the implementation agents
mode: primary
model: openai/gpt-5.5
temperature: 0.1
permission:
  read: allow
  glob: allow
  grep: allow
  list: allow
  edit: deny            # escreve via bash, nunca edição direta — mesmo padrão que já usas no workitem-conductor.md
  bash:
    "*": ask             # cada escrita cross-repo passa por confirmação explícita, igual ao que já acontece hoje
    "git status*": allow
    "git diff*": allow
    "git log*": allow
    "conductor plans*": allow   # consulta o registo sem pedir confirmação a cada leitura
  task: deny
---
```

Responsabilidades do prompt deste agente:

- Recebe (ou lê de um ficheiro — ver §4.2) a brief de domínio, em vez de depender de o utilizador a colar de cada vez.
- Conduz a conversa "descrição genérica → um ou mais planos", como já fazes hoje manualmente — incluindo, quando a análise identificar múltiplos repositórios afetados, escrever um plano por repositório na mesma sessão, todos com o mesmo `sprint:`.
- Sabe a estrutura obrigatória do corpo (Background, Goal, Out of scope, Tasks, Acceptance criteria, Risks) e o frontmatter obrigatório (§5).
- Corre `conductor plans list --sprint <sprint>` antes de finalizar, para saber que planos já existem no mesmo sprint e preencher `depends_on`/`related` corretamente — incluindo o `00-overview.md`, que passa a ser o primeiro ficheiro que esta própria conversa escreve (com `executable: false`), já com a tabela de planos filhos no frontmatter de cada um, em vez de reescrita à mão depois.

O `workitem-conductor` (ferramenta Python) não participa desta conversa em nenhum momento — só é consultado, passivamente, via `conductor plans list` (leitura).

### 4.2 Brief de domínio reutilizável (evita reescrever contexto arquitetural a cada conversa)

Uma conversa real de arranque cross-repo (a que gerou a sprint `claim-values`) começa com uma brief extensa e valiosa — modelo de dados (application/service/coverage), papel de cada um dos ~10 repositórios envolvidos, restrições da legacy (data-layer, não tocar tabelas legacy), serviços externos (interface automations). Esta brief não muda por sprint, mas hoje é reescrita/colada à mão em cada conversa nova — com risco real de esquecer um detalhe.

Proposta: capturar esta brief num ficheiro versionado (ex. `habit-tpaclaims-pyservice-layer/.ai/domain-brief.md`, ou um local central se fizer mais sentido para toda a plataforma habit) e o agente `plan-writer` referencia-o no arranque da conversa em vez de depender de o utilizador colar tudo de novo. Fica como item de acompanhamento, não bloqueante para o resto deste desenho.

### 4.3 No `workitem-conductor`: `conductor plans`

Substitui inteiramente o `Engine`/workitem lifecycle atual. Sub-comandos propostos:

- `conductor plans list [--sprint <nome>] [--repo <nome>]` — varre `.ai/execution_plans/**/*.md` nos repositórios registados, faz parse do frontmatter, mostra tabela (repo, ficheiro, estado, commit, depende-de). Substitui a tabela mantida à mão.
- `conductor plans lint [<id>]` — o comando mais importante do conjunto; sem ele os planos ficam soltos outra vez. Valida: `id` único (globalmente ou por sprint), `status` é um dos valores válidos, `depends_on`/`related` referenciam `id`s que existem, sem ciclos, `executable: false` nunca entra na lista de "pronto a executar", um plano `status: done` tem `commits` não-vazio, um plano não fica `done` se alguma dependência não estiver `done` (sem override explícito), e — regra de autossuficiência — todo plano com `external_handoff: true` tem de ter um resumo inline de cada `depends_on`/`related` no corpo, não só a referência.
- `conductor plans graph [--sprint <nome>]` — grafo de dependências; calcula "pronto a executar agora" (todas as dependências em `status: done`).
- `conductor plans mark <id> ready` — transição `draft` → `ready` (substitui o antigo `approve`, como campo de estado, não como comando com lógica própria).
- `conductor plans mark <id> done --commit <sha>` — acrescenta a `commits` (lista, não valor único — um plano pode gerar mais do que um commit, sobretudo cross-repo ou com fix posterior) e atualiza o frontmatter do ficheiro (não uma base de dados separada — o ficheiro continua a ser a fonte de verdade). `completed_at` é preenchido automaticamente por este comando, não escrito à mão.
- `conductor plans sync [<id>]` — descobre + normaliza + guarda snapshot, não é só uma query ao vivo. Corre a correlação contra `opencode.db` (ver nota sobre `PLAN_ID:` abaixo) e grava um snapshot normalizado em `~/.local/share/conductor/plans/<id>/opencode-sessions.json` — nunca dentro do repo do plano, para não poluir o histórico git desse repo com dados derivados de auditoria. `conductor plans list --with-execution` lê este snapshot, não o `opencode.db` diretamente, o que também protege contra o `opencode.db` mudar de schema/local/retenção entre o momento do `sync` e uma leitura posterior.
- `conductor plans table [--sprint <nome>]` — regenera a tabela markdown para colar num PR/partilhar com outra equipa.

**Correlação robusta, não só por path.** Confiar em `LIKE '%execution_plans%'` sobre o path do ficheiro é frágil — o ficheiro pode ser renomeado, o path pode ser relativo numa sessão e absoluto noutra, duas sessões podem referenciar o mesmo plano, uma sessão pode implementar só parte de um plano. Correção simples e de custo zero: o command `implement-plan.md` (já existente) passa a injetar sempre uma linha `PLAN_ID: <id>` (lido do frontmatter) no prompt, além do path — `conductor plans sync` procura primeiro por essa marca exata, com o path como fallback para planos antigos que ainda não tinham `id` na conversa que os executou.
- (mais tarde) `conductor export-audit --sprint <nome>` — pacote de auditoria: planos + sessões correlacionadas + diffs + estado das acceptance criteria.

---

## 5. Schema do frontmatter (proposta inicial, para validação)

```yaml
---
schema_version: 1                    # evolução do formato sem dor — nunca sem isto
id: claim-values-05-bugfixes         # slug estável, independente da data no filename
sprint: claim-values                  # agrupa planos da mesma iniciativa; opcional
primary_repo: habit-tpaclaims-pyservice-layer
status: draft | ready | in_progress | blocked | done | canceled
# draft = ainda a ser desenhado; ready = aprovado, pronto a executar (substitui o antigo "approve")
executable: true                      # false para ficheiros de índice (ex. "-00-overview.md")
commits: []                           # lista de shas — um plano pode gerar mais do que um commit
depends_on: []                        # lista de `id`s
related: []                           # lista de `id`s (não bloqueante, só contexto)
blocked_until: null                   # texto livre para gates não-plano (ex. "TPA em staging") — não validado automaticamente
owner_team: null                      # opcional; relevante quando outra equipa executa
external_handoff: false               # true obriga o `plans lint` a exigir resumo inline de cada depends_on/related no corpo
created_at: null                      # preenchido automaticamente, nunca escrito à mão
completed_at: null                    # idem, por `plans mark done`
---
```

O corpo do markdown **não é estruturado** — fica em prosa livre, exatamente como já se escreve hoje. Só o índice (frontmatter) é máquina-legível. Isto evita repetir o erro do `PlannerPhase` do código antigo (forçar texto livre a YAML rígido).

Campo em aberto, ainda não decidido: `repos_affected: [{repo, role}]` (papel por repositório, herdado do schema do V4/V5 — §1.6/§7). Fica preparado no schema mas não obrigatório nesta versão.

---

## 6. Fluxo de trabalho ponta a ponta

1. **Início** — problema/card genérico. No OpenCode: `/create-plan "melhorar o cálculo dos valores da claim" --repo habit-tpaclaims-pyservice-layer --sprint claim-values` (ou equivalente). Conversa até convergir no ficheiro final, frontmatter incluído.
2. **Registo** — nada a fazer explicitamente; `conductor plans list` varre o ficheiro assim que existe.
3. **Antes de executar** — `conductor plans ready --sprint claim-values` mostra o que já pode arrancar (dependências satisfeitas). `conductor plans lint` valida autossuficiência antes de entregar a outra equipa.
4. **Execução** — `/implement-plan` no OpenCode, sem alterações, sem o conductor no meio.
5. **Depois de executar** — `conductor plans mark <id> done --commit <sha>`.
6. **Reporting/auditoria** — `conductor plans table`/`conductor plans sync`/`conductor export-audit`.

---

## 7. Riscos e questões em aberto (para validação externa)

1. **`blocked_until` não-plano** (ex. "só arranca com TPA em staging") fica como texto livre, não validado. Aceitável, ou vale a pena um segundo tipo de gate (ex. `depends_on_deploy: <ambiente>`) validável por outro meio (webhook de CI, etc.)? Proposta atual: não vale a pena agora, é complexidade prematura.
2. **Resolvido**: `opencode.db` é um ficheiro local único, sem retenção conhecida — se o OpenCode alguma vez fizer vacuum/prune, perde-se histórico. `conductor plans sync` (§4.3) já não é só uma query ao vivo — descobre, normaliza e grava snapshot próprio em `~/.local/share/conductor/plans/<id>/`, para o `workitem-conductor` preservar evidência independente do `opencode.db` continuar íntegro.
3. **`id` como chave** — hoje os planos já usam nomes de ficheiro datados como identidade natural (`2026-07-15_...`). O `id` do frontmatter pode ser derivado do nome do ficheiro por omissão (menos fricção ao escrever) com override manual só quando necessário.
4. **Migração dos 20 planos já existentes na sprint `claim-values`** não têm frontmatter — precisam de ser retro-adaptados manualmente ou por um script de migração one-off (não vale a pena automatizar isto de forma sofisticada, é um custo único).
5. **Resolvido**: o `/create-plan` chama um agente `plan-writer` dedicado (§4.1), com escrita cross-repo via `bash: ask` (nunca `edit: allow`) — o `workitem-conductor` e os seus subagentes de implementação ficam inalterados, sem qualquer aumento de raio de ação. Falta só confirmar na prática se o padrão `bash: ask` gera confirmações a mais quando uma sessão escreve vários planos de seguida — se sim, considerar `"cat > */.ai/execution_plans/*": allow` como exceção pontual, mantendo tudo o resto pedido.
6. **`repos_affected` com papel por repo** (herdado do `workitem-state.schema.json` do V4/V5 — §1.6): o schema atual só tem `repo:` (um) + `depends_on`/`related` (outros planos). Para um plano cross-repo como o `2026-06-11_tpaclaims-claim-action-links` real (7 repositórios, papéis diferentes: `primary`, `operator_frontend`, `verification_only`, `legacy_reference_only`), pode valer a pena um campo `repos_affected: [{repo, role}]` explícito, em vez de inferir o papel de cada repo a partir de `depends_on` de outros planos. Ainda por decidir — não bloqueia o resto do desenho.
7. **Não existe agente de smoke-test/e2e**: hoje o `tester` (`~/.config/opencode/agents/tester.md`) só corre suites automatizadas (`pytest`/`npm test`/...), nunca sobe a aplicação nem valida comportamento real. O `workitem-conductor` (agente OpenCode) passou a propor `conductor plans mark <id> done --commit <sha>` como último passo do `/implement-plan` (2026-08-04), condicionado ao veredito `TEST_PASSED` do `tester` — ou seja, o `status: done` do plano herda esse mesmo limite de confiança (suíte automatizada, não verificação end-to-end). O texto da proposta de mark deve dizer isso explicitamente, para não sobre-declarar confiança no audit trail. Um agente `smoke-tester` dedicado (sobe a app, corre um fluxo real, reporta `SMOKE_PASSED`/`SMOKE_FAILED`) fica como item de roadmap — não bloqueia o resto do desenho, mas deve ser adicionado como gate extra ao `mark done` quando existir.

---

## 8. Sequência de implementação (marcos, não calendário)

Ordem por onde a dor é maior primeiro — não faz sentido construir `export-audit`/grafo/integração CI antes de a tabela manual estar resolvida:

1. **Registo — feito** (commits `52a88fd`/`086ceab`, mergeado em `main` 2026-08-04). `PlanFrontmatter` (pydantic), scanner de `.ai/execution_plans/**/*.md`, `plans list`, `plans lint`, `plans mark ready`/`mark done`, `plans table`.
2. **Correlação — feito** (2026-08-04). `PLAN_ID:` já injetado por `implement-plan.md` e confirmado ao vivo em sessões reais de produção. `plans sync [<id>]` lê `opencode.db` só para leitura (nunca escreve), procura o marcador exato por plano, junta as sessões-filho diretas (subagentes `implementer`/`reviewer`/`tester`/`committer`) e grava um snapshot normalizado em `data_home()/conductor/plans/<id>/opencode-sessions.json` — nunca uma query ao vivo. `plans list --with-execution` lê só esse snapshot. Testado contra dados reais: `decision-reason-dropdown` correlacionou corretamente 19 sessões (1 pai + 18 subagentes) com ~20,4M tokens. Nota: `cost` está a `0.0` para todas as sessões neste setup de contas — o sinal fiável são os tokens, não o custo. Não incluído nesta ronda: fallback por substring de path para planos antigos sem marcador (`matched_via: "none"` fica honesto em vez de adivinhar) — fica para depois, se necessário.
3. **Auditoria** — `export-audit --plan <id>`/`--sprint <nome>` (planos + sessões correlacionadas + commits + grafo de dependências).

`plans graph` pode nascer em qualquer um destes passos, conforme a necessidade concreta de visualizar dependências aparecer primeiro.

---

## 9. Não incluído neste documento

Desenho detalhado dos modelos pydantic e da implementação do `conductor plans` — fica para uma sessão de planeamento própria, depois desta validação externa.
