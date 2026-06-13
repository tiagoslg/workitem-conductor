# Plan: Application-scoped TPA Claims policy search contract

## Final decision
Use governed profile because this is a multi-repository claims/operator endpoint contract change with authorization and scope implications. Implement the backend contract first, then update the Configurator Hub consumer and validate both sides.

## Profile
- profile: `governed`
- role: `backend-implementer`
- mode: `full`
- primary repo: `habit-tpaclaims-pyservice-layer`
- frontend repo: `habit-agent-configurator-hub`


## Project / Branch Matrix
| Project | Will change? | Role | Suggested branch | Source branch | Notes |
| --- | --- | --- | --- | --- | --- |
| `habit-tpaclaims-pyservice-layer` | Yes | Primary backend contract | `feature/tpaclaims-operator-policy-claim-opening` | `main` | Add the application-scoped policies endpoint, structured filters, query tests, and preserve `policies.search` authorization. |
| `habit-agent-configurator-hub` | Yes | Frontend consumer | `feature/tpaclaims-operator-policy-claim-opening` | `main` | Update the policy opening service/page to select or receive an application context and call the new endpoint with structured filters. |
| `habit-care-pyservice-layer` | No | Out of scope | none | n/a | Do not alter unless implementation discovers a hard dependency, which should be escalated first. |
| `habit-care-pyservice-layer-fe` | No | Out of scope | none | n/a | Do not alter unless implementation discovers a hard dependency, which should be escalated first. |

## Analysis summary
Backend files already identified:
- `habit-tpaclaims-pyservice-layer/tpaclaims/views/collections/tpaclaims_policies.py`
- `habit-tpaclaims-pyservice-layer/tpaclaims/views/collections/tpaclaims_insurees.py`
- `habit-tpaclaims-pyservice-layer/tpaclaims/helpers/operator_policy_claim_actions.py`

Frontend files already identified:
- `habit-agent-configurator-hub/src/services/policyClaimOpening.ts`
- `habit-agent-configurator-hub/src/pages/claims/PolicyClaimOpeningPage.tsx`
- `habit-agent-configurator-hub/src/routes/AppRoutes.tsx`

## Implementation plan
1. Backend route contract
   - Add `GET /v3/tpa-claims/applications/{application_id:uuid}/policies` in a normalized endpoint file.
   - Keep endpoint file naming consistent with workspace rules, e.g. `tpaclaims_applications_policies.py`.
   - Preserve granular authorization with `policies.search`.
   - Decide whether the old global `/v3/tpa-claims/policies` should be removed, deprecated, or temporarily left unused. Since this is not a compatibility hotfix, prefer moving the FE to the new path and avoid relying on the global endpoint.

2. Backend query behavior
   - Scope by application through `clientservices.provider_id = application_id`.
   - Default to `p.state IN ('active')` when no state query param is provided.
   - Support repeated `state` params from `request.args.getlist('state')`.
   - Filter `distributor_id` using `p.cdata->>'distributor_id'`.
   - Filter `code` with exact match against `p.code` or `p.remote_code`.
   - Join policyholder data source once for filters instead of loading/filtering N+1 after query when feasible.
   - Support policyholder filters:
     - `name`: `ILIKE '%value%'`.
     - `email`: `ILIKE '%value%'`.
     - `phone`: suffix `LIKE '%value'`.
     - `document_number`: exact text match.
   - Return the existing policy object shape, including `policyholder`.

3. Frontend consumer
   - Update `searchOperatorPolicies` to require or accept `applicationId` and call `tpa-claims/applications/{applicationId}/policies`.
   - Replace generic `q` search with structured filter params.
   - Update `PolicyClaimOpeningPage` to require an application context before searching, using an existing application selector/context pattern if available.
   - Expose minimal operator filters needed for smoke testing: policy code, state, policyholder name/email/phone/document number, distributor id.
   - Preserve the existing draft/create/submit flow after a policy is selected.

4. Tests and validation
   - Add/update backend view/helper tests for application scoping, default active state, repeated state, distributor cdata filter, code/remote_code, and policyholder filters.
   - Add/update FE service tests if present; otherwise run TypeScript/build validation.
   - Validate workitem structure before handoff.

## Acceptance criteria
- `GET /v3/tpa-claims/applications/{application_id}/policies?code=FKF-1776` returns active policies for that application where `code` or `remote_code` equals `FKF-1776`.
- `state` defaults to active and repeated `state` params widen the state set explicitly.
- `distributor_id` uses `policies.cdata->>'distributor_id'`.
- Policyholder filters work as specified.
- Configurator Hub no longer calls `/v3/tpa-claims/policies` for opening-by-policy search.
- Existing policy-scoped claim journey/draft/submit endpoints remain usable after selecting a policy.

## Expected validations
- TPA Claims BE: `python3 -m py_compile` for altered files.
- TPA Claims BE: focused pytest for policy search helper/view behavior.
- Configurator Hub FE: `npm run build` or the repository's closest TypeScript validation.
- Root: `python3 .ai/tools/validate_workitem.py 2026-05-27_tpaclaims-application-policy-search-contract`.
- Root: `python3 .ai/tools/validate_runtime_structure.py`.
