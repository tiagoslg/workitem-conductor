"""Read-only cross-project dashboard (visibility track, phase B2).

Scans the registered workspaces and renders every workitem's state over a small
on-demand localhost web server. It never executes agents or writes anything — it
only reads the ``state.yml`` / ``goal.yml`` the engine already produces.
"""
