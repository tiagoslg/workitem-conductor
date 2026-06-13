# Policy search order_by follow-up

## Final decision
Implemented single-field `order_by` support for `GET /v3/tpa-claims/applications/{application_id}/policies` using `[+-]field` syntax.

## Contract
- Default ordering: `order_by=-created`.
- `order_by=-created` maps to `ORDER BY created DESC`.
- `order_by=+created` maps to `ORDER BY created ASC`.
- `order_by=created` is accepted and treated as `+created`.
- More than one `order_by` parameter is rejected with 412.
- Only returned scalar fields are accepted.

Allowed fields:
- `id`
- `policy_id`
- `created`
- `state`
- `quote_id`
- `clientservice_id`
- `application_id`
- `service_id`
- `user_id`
- `distributor_id`

`created` is now included in the endpoint response so the default/orderable field is part of the returned contract.

## Implementation notes
Because the query uses `DISTINCT ON (p.id)` to deduplicate policy rows, the SQL now deduplicates in an inner query using `ORDER BY p.id, p.created DESC`, then applies the API ordering in the outer query. This preserves deterministic row selection per policy and gives the caller meaningful list ordering.

## Files changed
- `habit-tpaclaims-pyservice-layer/tpaclaims/views/collections/tpaclaims_applications_policies.py`
- `habit-tpaclaims-pyservice-layer/tpaclaims/helpers/operator_policy_claim_actions.py`
- `habit-tpaclaims-pyservice-layer/tests/views/test_applications_policies_views.py`

## Validations
Executed in `habit-tpaclaims-pyservice-layer`:
- `python3 -m py_compile tpaclaims/views/collections/tpaclaims_applications_policies.py tpaclaims/helpers/operator_policy_claim_actions.py tests/views/test_applications_policies_views.py` — passed.
- `PYTHONPATH=. pytest -q tests/views/test_applications_policies_views.py` — passed: `35 passed, 3 warnings`.
