"""Registry, validation, and reporting for ``execution_plans/*.md`` files.

This is the whole of the conductor's remaining job: it never calls a model
and never executes anything. Plans are authored by OpenCode's ``plan-writer``
agent (or by hand) and executed by OpenCode's ``/implement-plan`` — the
conductor only reads, validates, and reports on the frontmatter those plans
already carry.
"""
