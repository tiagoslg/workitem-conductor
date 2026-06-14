# Implementation Notes — selfcare policy discoveries

**Work item**: `2026-06-01_tpaclaims-selfcare-policy-discoveries`  
**Branch**: `feature/tpaclaims-legacy-selfcare-access`  
**Commits**: `40bfa7e` (implementation), `ee50249` (review fix — atomic Redis TTL write)  
**Mode**: full  

---

## Implemented items

### Area 1 — Restrict normal selfcare policy list to direct distributor scope

File: `tpaclaims/helpers/operator_policy_claim_actions.py`

- Added `direct_only: bool = False` parameter to `_build_legacy_scope_sql()`.
- When `direct_only=True`, the scope clause uses `access_context.claim_direct_distributor_ids()` (returns only `self.distributor_ids` — no parent/sibling expansion).
- `search_application_policies()` now calls `_build_legacy_scope_sql(access_context, direct_only=True)`.
- Seller and dp-restricted contexts are unchanged (they never expand regardless).

### Area 2 — New `POST /v3/tpa-claims/applications/{application_id}/policy-discoveries` endpoint

File created: `tpaclaims/views/collections/tpaclaims_applications_policy_discoveries.py`  
Registration: `tpaclaims/views/collections/__init__.py` (line after `ApplicationsPoliciesCollection`)

- `ApplicationsPolicyDiscoveriesCollection.post()` handles both selfcare and operator contexts.
- Validates that `code` and `identifier` body fields are present (422 if missing).
- For selfcare: calls `find_policy_for_selfcare_discovery()`, enforces `PERM_DISCOVERABLE_PARENT_DISTRIBUTOR` when the policy is from a sibling distributor (403 `tpa_policy_discovery_not_allowed` if permission absent), writes Redis discovery record atomically with TTL (see Area 3), returns `discovery_id`, `expires_at`, `ttl_seconds` alongside the full policy payload.
- For operator `AccessContext`: calls `search_application_policies()` with the existing direct scope; returns matching policy without Redis write (`discovery_id=None`).
- Responds `403 tpa_policy_discovery_not_allowed` when no matching policy is found or access is not allowed. This is a deliberate **anti-enumeration** contract: callers cannot distinguish "policy does not exist" from "policy exists but you are not authorized to discover it".

### Area 3 — Write Redis discovery auth records

File: `tpaclaims/helpers/operator_policy_claim_actions.py`

- Added `_is_selfcare_policy_discovery_valid(platform_user_id, application_id, policy_id)` helper.
  - Reads from `redisc.client("habit")`.
  - Key format: `TPA/selfcare-policy-discoveries/{platform_user_id}/{application_id}/{policy_id}`.
  - **Existence-only** check: `rclient.exists(key)` is sufficient. The key namespace already binds `platform_user_id + application_id + policy_id`; TTL is the authorization boundary. No value inspection (`status == "active"`) is performed.
- Discovery endpoint writes this key **atomically** with `TTL=3600s` using `rclient.set(key, value, ex=_DISCOVERY_TTL)` — a single atomic call. This was the fix applied in review commit `ee50249` (original `40bfa7e` used a non-atomic split `set()` + `expire()` which could leave a non-expiring key if `expire()` failed).

### Area 4 — Allow previously-discovered sibling policies through existing policy-scoped endpoints

File: `tpaclaims/helpers/operator_policy_claim_actions.py`

- `get_operator_policy()`: after fetching the policy via the existing `_build_legacy_direct_policy_sql()` (which already uses `effective_distributor_ids()` and may return sibling policies):
  - Detects sibling: `policy_dist_id in eff_ids AND policy_dist_id not in direct_ids`.
  - For selfcare sibling: calls `_is_selfcare_policy_discovery_valid()`.  
    - If not valid → 403 `tpa_policy_discovery_expired_or_missing`.
    - If valid → access granted normally.
- Added `find_policy_for_selfcare_discovery()` helper using `_application_policy_base_query()` + `direct_only=False` (sibling-expanded) scope. Searches by code+identifier EXISTS subquery.

### Constants

File: `tpaclaims/constants.py`

```python
ERROR_TPA_POLICY_DISCOVERY_NOT_ALLOWED = "tpa_policy_discovery_not_allowed"
ERROR_TPA_POLICY_DISCOVERY_EXPIRED = "tpa_policy_discovery_expired_or_missing"
```

---

## Tests

| Suite | Before | After | Delta |
|---|---|---|---|
| `tests/views/test_applications_policies_views.py` | 89 | 97 | +8 |
| `tests/views/test_applications_policy_discoveries_views.py` | 0 | 17 | +17 (new) |
| `tests/unit/test_helpers/test_operator_policy_claim_actions_queue.py` | — | — | +7 (new class) |
| `tests/views/test_applications_policies_claim_opening_views.py` | 89 | 89 | 0 regression |
| `tests/unit/test_helpers/test_legacy_selfcare_access.py` | — | — | 0 regression |

**Total across all 5 suites: 211 passed, 0 failed.**

Two existing tests in `TestDiscoverableParentPoliciesViewPath` were renamed and inverted:
- `test_discoverable_ctx_sibling_ids_included_in_search_sql` → `test_discoverable_ctx_sibling_ids_not_in_search_sql`
- `test_visible_ctx_sibling_ids_included_in_search_sql` → `test_visible_ctx_sibling_ids_not_in_search_sql`

These now correctly assert that sibling IDs do NOT appear in normal policy list SQL (direct_only=True).

Pre-existing failures in unrelated test files (`test_financial_movement_actions_removed.py`, `test_claim_case_workspace_actions.py`, `test_claim_case_create_actions.py`) confirmed present before this branch's changes — not caused by this work.

---

## Deviations from plan

None. All four areas implemented as specified in `handoff.md` and `task.md`.

---

## Blockers

None.

---

## Follow-up risks

- Redis `redisc.client("habit")` TTL is 3600 s (1 h). If product requires a different TTL, it is a single constant change in the discovery endpoint file.
- `find_policy_for_selfcare_discovery()` searches by `code` (policy product code) + `identifier` (document number via insuree subquery). If discovery needs to support additional identifier types (e.g. policy number), that is an extension to the EXISTS subquery.
- The sibling-detection logic in `get_operator_policy()` relies on `effective_distributor_ids()` including sibling IDs for `PERM_DISCOVERABLE_PARENT_DISTRIBUTOR` contexts. Any future change to that permission's expansion must be tested against this path.

---

## Final decision

- mode: full
- final_status: reviewed_approved
- next_stage: release-integration-validator
- review_verdict: pass_with_minors
- review_fix_commit: ee50249
- retry_relevant: no
- human_intervention_required: no
