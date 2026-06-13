# Reviewer handoff: 2026-06-01_tpaclaims-selfcare-policy-discoveries

## Final decision
Review the BE implementation for application-scoped selfcare policy discoveries in `habit-tpaclaims-pyservice-layer`, commit `40bfa7e`. The implementation is ready for backend review after lead validation; focused tests pass locally.

## Target
- Workitem: `2026-06-01_tpaclaims-selfcare-policy-discoveries`
- Parent: `2026-05-27_tpaclaims-capability-code-standardization`
- Repository: `habit-tpaclaims-pyservice-layer`
- Branch: `feature/tpaclaims-legacy-selfcare-access`
- Commit under review: `40bfa7e`
- Profile: governed
- Recommended agent: `backend-reviewer`, full mode

## Implemented surface
Files changed by the commit:

```text
tpaclaims/constants.py
tpaclaims/helpers/operator_policy_claim_actions.py
tpaclaims/views/collections/__init__.py
tpaclaims/views/collections/tpaclaims_applications_policy_discoveries.py
tests/views/test_applications_policy_discoveries_views.py
tests/views/test_applications_policies_views.py
tests/views/test_applications_policies_claim_opening_views.py
tests/unit/test_helpers/test_operator_policy_claim_actions_queue.py
```

Main behavior implemented:

- `GET /v3/tpa-claims/applications/{application_id}/policies` now uses direct-only legacy/selfcare distributor scope for normal listing.
- New endpoint:

```http
POST /v3/tpa-claims/applications/{application_id}/policy-discoveries
```

- Selfcare discovery requires `code + identifier`.
- Sibling-distributor discovery requires `io.habit.access.discoverable.parent_distributor_sales.all`.
- Successful selfcare discovery writes Redis key with TTL `3600`.
- Follow-up policy-scoped endpoints should work through `get_operator_policy()` when either direct scope applies or the Redis discovery key exists.

## Lead validation performed
Local working tree in `habit-tpaclaims-pyservice-layer` was clean.

Ran:

```bash
PYTHONPATH=. pytest tests/views/test_applications_policy_discoveries_views.py tests/views/test_applications_policies_views.py tests/views/test_applications_policies_claim_opening_views.py tests/unit/test_helpers/test_operator_policy_claim_actions_queue.py tests/unit/test_helpers/test_legacy_selfcare_access.py -q
```

Result:

```text
211 passed, 3 warnings in 0.66s
```

Also ran:

```bash
git show --check 40bfa7e
```

Result: one minor formatting issue only:

```text
tests/views/test_applications_policies_views.py:1148: new blank line at EOF.
```

## Review focus
Please review these items carefully. They are the highest-risk parts of the change.

1. Normal selfcare policy list must not expose sibling distributor policies.
   - Confirm `search_application_policies()` calls `_build_legacy_scope_sql(..., direct_only=True)`.
   - Confirm neither `visible.parent_distributor_sales.all` nor `discoverable.parent_distributor_sales.all` broadens the normal list.

2. Discovery endpoint must be the only sibling-distributor policy discovery channel.
   - Confirm `find_policy_for_selfcare_discovery()` requires exact `code` or `remote_code` and exact document identifier.
   - Confirm sibling discovery is allowed only with `PERM_DISCOVERABLE_PARENT_DISTRIBUTOR`.
   - Confirm `PERM_VISIBLE_PARENT_DISTRIBUTOR` alone does not allow sibling discovery unless product explicitly changes that rule.

3. Follow-up authorization after discovery.
   - Confirm all policy-scoped claim-opening paths use `get_operator_policy()` and therefore enforce the Redis discovery key for sibling policies:

```http
GET  /v3/tpa-claims/applications/{application_id}/policies/{policy_id}/claim-journey-specs
POST /v3/tpa-claims/applications/{application_id}/policies/{policy_id}/claims
POST /v3/tpa-claims/applications/{application_id}/policies/{policy_id}/claims/{claim_id}/submit
```

4. Redis validity semantics.
   - Implementation currently treats `rclient.exists(key)` as the validity check.
   - The Redis value does not include `status: active`, despite the implementation notes saying the helper returns true only when status is active.
   - This may be acceptable because TTL is the authorization boundary, but the reviewer should decide whether existence-only is sufficient or whether value inspection is required.

5. Error contract.
   - Handoff expected a clear not-found behavior; implementation returns `tpa_policy_discovery_not_allowed` with 403 for no match.
   - This may be desirable to avoid policy enumeration, especially for selfcare, but it differs from the implementation notes line saying 404.
   - Reviewer should decide whether 403 is the intended final contract and make the artifact/code comments consistent.

6. Restricted seller/distribution-point contexts.
   - `_build_legacy_scope_sql()` still ORs direct `distributor_id` with `distributorseller_id`/`distributionpoint_id` predicates.
   - This is pre-existing behavior, but because this work touches selfcare policy listing, confirm it cannot broaden `restricted_ownsales` or `restricted_distributionpoints_sales` beyond the intended selfcare behavior returned by habit-utils.

7. Endpoint placement and naming.
   - Endpoint file is under `views/collections/` as `tpaclaims_applications_policy_discoveries.py`.
   - This matches the chosen resource shape `policy-discoveries`; verify route registration/import convention is acceptable.

## Suggested reviewer tests
At minimum rerun the same focused suite from lead validation. If time allows, add or request an integration-style test that calls the actual journey/create/submit view wrappers for a sibling policy with and without Redis key, not only the lower-level `get_operator_policy()` helper.

## Expected review artifact
Create:

```text
.ai/workitems/2026-06-01_tpaclaims-selfcare-policy-discoveries/artifacts/review.md
```

The review artifact must include:

```md
## Final decision
```

If approved, update:

- `state.json.status = reviewed_approved`
- `state.json.next_action = proceed with FE policy discovery child or integration once FE is complete`
- `state.json.canonical_artifact = .ai/workitems/2026-06-01_tpaclaims-selfcare-policy-discoveries/artifacts/review.md`
- `.ai/index.json` entry if tracked

If must-fix findings exist, set status to `review_changes_requested` and point `canonical_artifact` to the review artifact.
