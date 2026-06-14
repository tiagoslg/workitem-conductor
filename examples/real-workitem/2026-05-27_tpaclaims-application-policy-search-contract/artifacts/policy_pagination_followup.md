## Final decision

Mode: focused.

Application-scoped policy search now supports simple `limit` + `offset` pagination without a `total` count.

## Changes

- Backend repo: `habit-tpaclaims-pyservice-layer`
- Branch: `feature/tpaclaims-operator-policy-claim-opening`
- Commit: `d813c4c`
- Endpoint affected: `GET /v3/tpa-claims/applications/{application_id}/policies`
- Added `offset` query parameter.
- Kept `limit` capped to `1..50`.
- Normalized negative `offset` to `0`.
- SQL now applies `LIMIT %(limit)s OFFSET %(offset)s` after the selected `order_by`.
- Response envelope now includes `limit` and `offset` with existing `elements` and `size`.
- No `total` count was added by product decision.

## Response envelope

```json
{
  "elements": [],
  "size": 0,
  "limit": 25,
  "offset": 0
}
```

Example calls:

```http
GET /v3/tpa-claims/applications/<application_id>/policies?limit=25&offset=0
GET /v3/tpa-claims/applications/<application_id>/policies?limit=25&offset=25
```

## Files changed

- `habit-tpaclaims-pyservice-layer/tpaclaims/helpers/operator_policy_claim_actions.py`
- `habit-tpaclaims-pyservice-layer/tpaclaims/views/collections/tpaclaims_applications_policies.py`
- `habit-tpaclaims-pyservice-layer/tests/views/test_applications_policies_views.py`

## Validations executed

- `python3 -m py_compile tpaclaims/helpers/operator_policy_claim_actions.py tpaclaims/views/collections/tpaclaims_applications_policies.py tests/views/test_applications_policies_views.py`
- `PYTHONPATH=. pytest -q tests/views/test_applications_policies_views.py` -> 42 passed, 3 warnings

## Remaining gaps

- Needs backend redeploy of commit `d813c4c` before smoke retest.
- No FE change was made in this pass; the API is ready for a UI consumer to request subsequent pages.

## Recommendation

Proceed with backend redeploy and smoke retest using `limit` and `offset` query parameters.
