# Backend review — 2026-06-01_tpaclaims-selfcare-policy-discoveries

Reviewed repo: `habit-tpaclaims-pyservice-layer`
Branch: `feature/tpaclaims-legacy-selfcare-access`
Commit under review: `40bfa7e`
Review fix commit: `ee50249`

## Summary verdict

`pass_with_minors`

The implementation meets the intended scope after one review-found security fix was applied inline: the discovery Redis authorization key is now written atomically with its TTL, avoiding a partial-write path that could have left a non-expiring authorization key behind.

## Scope checks

- **Normal selfcare list restriction** confirmed:
  - `search_application_policies()` calls `_build_legacy_scope_sql(..., direct_only=True)` — `operator_policy_claim_actions.py:496-503`
  - `direct_only=True` uses `claim_direct_distributor_ids()`; visible/discoverable parent permissions do not broaden the normal list
  - covered by regressions in `test_applications_policies_views.py:1095-1146`

- **Discovery endpoint as only sibling-distributor channel** confirmed:
  - exact code/remote_code + exact identifier match in `find_policy_for_selfcare_discovery()` — `operator_policy_claim_actions.py:567-580`
  - sibling discovery requires `PERM_DISCOVERABLE_PARENT_DISTRIBUTOR` — `tpaclaims_applications_policy_discoveries.py:123-136`
  - `PERM_VISIBLE_PARENT_DISTRIBUTOR` alone does not authorize sibling discovery

- **Follow-up authorization** confirmed — all three paths flow through `get_operator_policy()`:
  - journey spec → `get_policy_claim_journey_spec()` — `operator_policy_claim_actions.py:669-680`
  - draft create → `create_policy_claim_draft()` — `operator_policy_claim_actions.py:683-696`
  - submit → `submit_policy_claim_declaration()` — `operator_policy_claim_actions.py:733-746`
  - sibling follow-up guarded by Redis existence check — `operator_policy_claim_actions.py:198-214`

- **Restricted seller/DP context** — no new broadening introduced; parent/sibling expansion still gated when seller or DP restrictions exist — `legacy_selfcare_access.py:130-139`

- **Endpoint placement** — file, location, and naming consistent with existing collection patterns; registered in `views/collections/__init__.py:86-89`

## Findings

| # | Severity | Status | Title | File / line | Impact | Fix |
|---|---|---|---|---|---|---|
| 1 | **must-fix** | ✅ resolved in `ee50249` | Discovery Redis TTL write was non-atomic | `tpaclaims_applications_policy_discoveries.py:169-173` | Original `set()` + `expire()` split could leave a non-expiring Redis key if second call failed — turning a 1h authorization into indefinite access | Fixed with atomic `set(..., ex=_DISCOVERY_TTL)` |
| 2 | minor (artifact) | open | `implementation_notes.md` describes outdated Redis/error-contract semantics | `artifacts/implementation_notes.md:28-30,36-40` | Notes say "404 not found" and "status == active" value inspection; shipped behavior is deliberate 403 anti-enumeration and existence-only validity | Refresh notes to match final behavior |

## Redis validity decision

Existence-only is sufficient for authorization **provided the TTL is written atomically** (now guaranteed after fix `ee50249`).

Rationale:
- the key namespace already binds `platform_user_id + application_id + policy_id`
- TTL is the actual authorization boundary
- the stored JSON value is useful for audit/debug metadata but does not need to be re-read to make the access decision

## Error-contract decision

`403 tpa_policy_discovery_not_allowed` is the correct final contract for "not found / not allowed" outcomes.

This is a **deliberate anti-enumeration choice**: it avoids revealing whether a policy exists outside the caller's allowed discovery scope, and keeps "wrong code/identifier" indistinguishable from "not authorized to discover sibling policy". This should be documented explicitly in `implementation_notes.md`.

## Risk notes

- No remaining blocker after the Redis TTL write fix.
- No dedicated integration-style test invoking journey/create/submit view wrappers with/without a Redis key — helper-level enforcement is covered and wrapper sources correctly pass `application_id`. Useful follow-up but not a release blocker.

## Test evidence

```bash
PYTHONPATH=. pytest tests/views/test_applications_policy_discoveries_views.py \
  tests/views/test_applications_policies_views.py \
  tests/views/test_applications_policies_claim_opening_views.py \
  tests/unit/test_helpers/test_operator_policy_claim_actions_queue.py \
  tests/unit/test_helpers/test_legacy_selfcare_access.py -q
```

Result: **211 passed, 3 warnings**

## Final decision

- verdict: `pass_with_minors`
- review fix commit: `ee50249`
- next step: proceed with FE policy discovery child or integration validation once FE is complete
- human_intervention_required: no
