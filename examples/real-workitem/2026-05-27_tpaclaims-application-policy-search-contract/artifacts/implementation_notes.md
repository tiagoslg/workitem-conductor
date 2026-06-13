# Implementation notes — 2026-05-27_tpaclaims-application-policy-search-contract

## Status
Implementation complete. Commits on `feature/tpaclaims-operator-policy-claim-opening`.

## Files changed

| File | Change |
|---|---|
| `tpaclaims/helpers/operator_policy_claim_actions.py` | Added `search_application_policies()`, `_application_policy_base_query()`, imported `_batch_load_policyholders_for_policies` |
| `tpaclaims/views/collections/tpaclaims_applications_policies.py` | New view — `ApplicationsPoliciesCollection` |
| `tpaclaims/views/collections/__init__.py` | Registered new view; removed `PoliciesCollection` |
| `tpaclaims/views/collections/tpaclaims_policies.py` | Deleted (legacy endpoint removed) |
| `tests/views/test_applications_policies_views.py` | New — 23 tests |

## Endpoint added

```
GET /v3/tpa-claims/applications/{application_id:uuid}/policies
```

Query params: `state` (repeatable), `distributor_id`, `code`, `name`, `email`, `phone`, `document_number`, `limit`

## Application scoping

`AND cs.provider_id = %(application_id)s` in WHERE clause via `clientservices` JOIN.

## distributor_id

Sourced from `p.cdata->>'distributor_id'` in both SELECT and filter. No dependency on `quotes.distributor_id`.

## code / remote_code

Exact match: `AND (p.code = %(code)s OR p.remote_code = %(code)s)`

## Policyholder filtering

SQL JOIN path: `policies → insureequotes → insurees → insureeproperties`

Inline in the base query via `_POLICYHOLDER_JOIN_SQL`. Filters applied as:
- `name`: `ip.data::text ILIKE %val%`
- `email`: `ip.data::text ILIKE %val%`
- `phone`: `ip.data::text LIKE %val` (suffix)
- `document_number`: `ip.data::text LIKE %val%`

Batch enrichment uses `_batch_load_policyholders_for_policies` (2 queries per page, no N+1).

## Default state

`["active"]` when no `state` param provided. Multiple states via `request.args.getlist("state")`.

## Legacy endpoint

`GET /v3/tpa-claims/policies` removed. Product is not in production; FE update planned in sequence.

## Commits

- `8985a25` feat: add application-scoped policy search endpoint
- `7233c28` feat: remove legacy /v3/tpa-claims/policies global search endpoint

## Test results

```
PYTHONPATH=. pytest -q tests/views/test_applications_policies_views.py
→ 23 passed

PYTHONPATH=. pytest -q tests/views/test_applications_policies_views.py tests/views/test_loader_registration_regression.py
→ 72 passed

python3 .ai/tools/validate_workitem.py 2026-05-27_tpaclaims-application-policy-search-contract
→ Work item validation passed

python3 .ai/tools/validate_runtime_structure.py
→ Runtime structure validation passed
```

## Final decision

Implementation complete and committed on branch `feature/tpaclaims-operator-policy-claim-opening`.
Next step: backend review, then FE implementation on `habit-agent-configurator-hub`.
