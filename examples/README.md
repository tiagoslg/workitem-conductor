# Examples — reference workitem shapes

The `real-workitem/` directories are **real workitems** carried over from the
predecessor project (`tg-habit-ai-orchestrator`, a TPA Claims codebase). They are
kept only as *reference shapes* — to illustrate the kinds of artifacts a workitem
accumulates over its lifecycle (task, handoff, plan, implementation notes,
reviews, deploy report, state).

They are **not** part of the generic tool, are **not** consumed by the CLI, and
their structure is intentionally heavier than what `conductor` produces today.
The conductor's own, deliberately minimal layout is `goal.yml` + `state.yml` per
workitem (see the project README and `conductor init`).
