# Merge/deploy checkpoint — application-scoped policy search contract

## Final decision
Merge/deploy checkpoint is conditionally acceptable for smoke progression based on local branch validation: backend and frontend feature branches contain the requested contract changes, focused backend tests pass, and frontend production build passes. I could not independently confirm the deployed environment or browser Network tab; deploy commit/hash confirmation remains a human/environment check.

## Scope validated
Work item: `2026-05-27_tpaclaims-application-policy-search-contract`

Repositories checked locally:
- `habit-tpaclaims-pyservice-layer`
  - branch: `feature/tpaclaims-operator-policy-claim-opening`
  - local HEAD: `9a6ab24`
- `habit-agent-configurator-hub`
  - branch: `feature/tpaclaims-operator-policy-claim-opening`
  - local HEAD: `42493eb`

Merge-state note:
- Local `develop` does not currently contain either feature HEAD (`merge-base --is-ancestor HEAD develop` returned `1` in both repositories).
- This validates the feature branch content, not a local checkout of merged `develop`.

## Backend checkpoint
Confirmed in `habit-tpaclaims-pyservice-layer`:
- `GET /v3/tpa-claims/applications/{application_id:uuid}/policies` exists in `tpaclaims/views/collections/tpaclaims_applications_policies.py`.
- The old global collection endpoint `GET /v3/tpa-claims/policies` is absent. Remaining `/v3/tpa-claims/policies/{policy_id:uuid}/...` routes are policy sub-resource routes and are unrelated.
- `document_number` exact match fix is present:
  - uses an `EXISTS` subquery scoped to document-like namespaces;
  - uses equality against `%(ph_doc)s`;
  - does not use wildcard/substring matching for document_number.
- Application scoping uses `cs.provider_id = %(application_id)s`.
- Default state is active through `_ACTIVE_STATES = ["active"]`.
- Repeated states are accepted through `request.args.getlist("state")`.
- `distributor_id` uses `p.cdata->>'distributor_id'`.
- `code` matches `p.code = %(code)s OR p.remote_code = %(code)s`.

Backend validations executed:
- `python3 -m py_compile tpaclaims/views/collections/tpaclaims_applications_policies.py tpaclaims/helpers/operator_policy_claim_actions.py` — passed.
- `PYTHONPATH=. pytest -q tests/views/test_applications_policies_views.py` — passed: `25 passed, 3 warnings`.

Backend deploy/hash note:
- Candidate branch commit validated locally: `9a6ab24`.
- Deployed environment/hash was not independently verified from this workspace.

## Frontend checkpoint
Confirmed in `habit-agent-configurator-hub`:
- `searchOperatorPolicies(applicationId, filters, token)` calls `tpa-claims/applications/${applicationId}/policies`.
- No `q` parameter is used by the policy-opening search function/page.
- The old global policy search call is absent from the opening search flow.
- The service still uses `/policies/{policyId}/...` for policy sub-resource actions: claims, journey specs, assets, submit. These are expected and not the removed collection endpoint.
- `PolicyClaimOpeningPage` uses `usePersistedApplication()` and `ApplicationSelect` before enabling policy search.
- Structured filters are present: `state`, `code`, `distributor_id`, `name`, `email`, `phone`, `document_number`.

Frontend validations executed:
- `npm run build` — passed.
- `npm run lint` — failed due known tooling crash in ESLint/esquery while linting `config.ts`: `SyntaxError: Invalid regular expression: /\/: \ at end of pattern`. This is not a feature code finding and matches the existing frontend lint tooling issue already tracked separately.

Frontend deploy/hash note:
- Candidate branch commit validated locally: `42493eb`.
- Deployed environment/hash was not independently verified from this workspace.

## Post-deploy browser checklist
Not executed from this workspace. Human/browser validation still needed:
- Hard refresh / clear Hub cache.
- Confirm Network tab shows calls shaped as `/v3/tpa-claims/applications/{application_id}/policies?...`.
- Confirm Network tab does not show the old collection call `/v3/tpa-claims/policies`.
- Confirm Hub points at the backend environment that includes backend commit `9a6ab24` or equivalent merged/deployed commit.

## Gaps / cautions
- Local `develop` does not contain the feature HEADs, so if the merge already happened remotely, local refs were not updated here.
- Actual smoke deployment and browser Network tab cannot be confirmed from local static validation.
- FE lint remains blocked by the known ESLint/esquery tooling crash; production build passes.
