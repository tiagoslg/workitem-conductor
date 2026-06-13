# Execution Brief: Application-scoped TPA Claims policy search contract

## Final decision
Implement a clean application-scoped policy search contract for operator claim opening. Do not continue extending the current free-text global `/policies?q=...` behavior as the main flow.


## Project / Branch Matrix
| Project | Will change? | Role | Suggested branch | Source branch | Notes |
| --- | --- | --- | --- | --- | --- |
| `habit-tpaclaims-pyservice-layer` | Yes | Primary backend contract | `feature/tpaclaims-operator-policy-claim-opening` | `main` | Add the application-scoped policies endpoint, structured filters, query tests, and preserve `policies.search` authorization. |
| `habit-agent-configurator-hub` | Yes | Frontend consumer | `feature/tpaclaims-operator-policy-claim-opening` | `main` | Update the policy opening service/page to select or receive an application context and call the new endpoint with structured filters. |
| `habit-care-pyservice-layer` | No | Out of scope | none | n/a | Do not alter unless implementation discovers a hard dependency, which should be escalated first. |
| `habit-care-pyservice-layer-fe` | No | Out of scope | none | n/a | Do not alter unless implementation discovers a hard dependency, which should be escalated first. |

## Start here
1. Branch both changed repositories from their expected integration source using branch `feature/tpaclaims-operator-policy-claim-opening`.
2. Backend first: implement the new application-scoped route and helper signature.
3. Frontend second: update the opening page/service to call the new path with structured filters.
4. Keep lifecycle behavior untouched.

## Backend notes
The current helper is in `tpaclaims/helpers/operator_policy_claim_actions.py`:
- `_policy_base_query()` currently joins `policies p`, `clientservices cs`, and `quotes q`.
- `search_operator_policies()` currently accepts `query` and maps it to `ILIKE` over `p.id`, `p.user_id`, and `q.distributor_id`.
- `_serialize_policy()` currently calls `_load_policyholder_for_policy(policy_id)`, which may be acceptable for response enrichment, but filtering by policyholder should avoid broad post-query filtering when possible.

## Frontend notes
The current Hub service is `src/services/policyClaimOpening.ts`:
- `searchOperatorPolicies(params: { q?: string; limit?: number }, token)` calls `${BASE}/policies`.
- `PolicyClaimOpeningPage.tsx` stores one generic `search` string and passes it as `q`.
- The page needs an application context or selector so it can call `/applications/{application_id}/policies`.

## Risks to watch
- `policies.cdata->>'distributor_id'` may diverge from `quotes.distributor_id`; the human request explicitly wants policy cdata for this filter.
- Policyholder attributes may live through `policies -> insureequotes -> insurees -> insureeproperties`; inspect existing `_load_policyholder_for_policy()` behavior before writing filter SQL.
- Repeated state params require `request.args.getlist('state')`, not `request.args.get('state')`.
- The old global endpoint may still be used by other callers; do not remove it without checking references.

## Recommended next action
`backend-implementer_implement_application_policy_search_contract`
