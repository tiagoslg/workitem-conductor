# Application-scoped TPA Claims policy search contract

## Final decision
Create a new governed workitem for the policy search contract correction. This is not a hotfix and should be implemented as a focused BE/FE contract change across TPA Claims BE and Configurator Hub FE.

## Human request
The current operator policy search endpoint is wrong for the intended opening-by-policy flow. Replace the global policy search shape with an application-scoped endpoint and explicit filters.

## Required API contract
- New policy search path: `GET /v3/tpa-claims/applications/{application_id}/policies`.
- The `application_id` path segment must scope results to policies belonging to that application.
- Default state filter: return only `state=active` when no state is provided.
- Repeated `state` query params must be supported, e.g. `?state=active&state=terminated`.
- `distributor_id` must filter from `policies.cdata->>'distributor_id'`.
- `code` must match `policies.code == value OR policies.remote_code == value`.
- Policyholder filters must be supported because the response already includes policyholder data:
  - `name`: partial case-insensitive match.
  - `email`: partial case-insensitive match.
  - `phone`: suffix match, equivalent to `LIKE '%{phone}'`.
  - `document_number`: exact full-field match.

## Current behavior found during analysis
- Current BE endpoint: `GET /v3/tpa-claims/policies`.
- Current FE route: `/claims/open-policy` calls `searchOperatorPolicies({ q, limit })`.
- Current BE helper treats `q` as free text and searches only `p.id`, `p.user_id`, and `quotes.distributor_id`.
- Current query does not parse `q=code=...`, does not search `policies.code`, does not search `policies.remote_code`, and is not application-scoped.

## Repositories
- `habit-tpaclaims-pyservice-layer`: primary backend contract and tests.
- `habit-agent-configurator-hub`: frontend consumer update for policy opening search.

## Project / Branch Matrix
| Project | Will change? | Role | Suggested branch | Source branch | Notes |
| --- | --- | --- | --- | --- | --- |
| `habit-tpaclaims-pyservice-layer` | Yes | Primary backend contract | `feature/tpaclaims-operator-policy-claim-opening` | `main` | Add the application-scoped policies endpoint, structured filters, query tests, and preserve `policies.search` authorization. |
| `habit-agent-configurator-hub` | Yes | Frontend consumer | `feature/tpaclaims-operator-policy-claim-opening` | `main` | Update the policy opening service/page to select or receive an application context and call the new endpoint with structured filters. |
| `habit-care-pyservice-layer` | No | Out of scope | none | n/a | Do not alter unless implementation discovers a hard dependency, which should be escalated first. |
| `habit-care-pyservice-layer-fe` | No | Out of scope | none | n/a | Do not alter unless implementation discovers a hard dependency, which should be escalated first. |


## Out of scope
- Do not change claim lifecycle semantics.
- Do not treat as hotfix.
- Do not change payment flows.
- Do not alter Care BE/FE unless implementation discovers a hard dependency.
