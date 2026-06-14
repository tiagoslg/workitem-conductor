## Final decision

Mode: focused.

Application-scoped policy search now returns additive `service` and `distributor` objects while preserving the existing top-level `service_id` and `distributor_id` fields.

## Changes

- Backend repo: `habit-tpaclaims-pyservice-layer`
- Branch: `feature/tpaclaims-operator-policy-claim-opening`
- Commit: `29830e7`
- Endpoint affected: `GET /v3/tpa-claims/applications/{application_id}/policies`
- Added service enrichment from `services` via `clientservices.service_id`.
- Added distributor enrichment from `distributors` via the endpoint's canonical distributor source: `policies.cdata->>'distributor_id'`.
- Kept `service_id` and `distributor_id` as top-level fields for compatibility with the current UI contract.
- Did not change filters, ordering, lifecycle, capabilities, payments, or FE code.

## Response contract

Each policy row may now include:

```json
{
  "service": {
    "id": "<service_id>",
    "service_id": "<service_id>",
    "name": "<service name>",
    "segment_id": "<segment_id>"
  },
  "distributor": {
    "id": "<distributor_id>",
    "distributor_id": "<distributor_id>",
    "name": "<distributor name>"
  }
}
```

If the associated id is missing, the nested object is `null`.

## Files changed

- `habit-tpaclaims-pyservice-layer/tpaclaims/helpers/operator_policy_claim_actions.py`
- `habit-tpaclaims-pyservice-layer/tests/views/test_applications_policies_views.py`

## Validations executed

- `python3 -m py_compile tpaclaims/helpers/operator_policy_claim_actions.py tests/views/test_applications_policies_views.py`
- `PYTHONPATH=. pytest -q tests/views/test_applications_policies_views.py` -> 37 passed, 3 warnings

## Remaining gaps

- Needs redeploy of backend commit `29830e7` before smoke retest.
- No FE change was required because this is an additive backend response contract.

## Recommendation

Proceed with backend redeploy and smoke retest of the application-scoped policy search endpoint.
