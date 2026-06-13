# Implementer handoff: 2026-06-01_tpaclaims-selfcare-policy-discoveries

## Final decision
Implement the BE policy discovery contract in `habit-tpaclaims-pyservice-layer`: normal application policy listing must not expose sibling-distributor policies to legacy/selfcare users, while `POST /v3/tpa-claims/applications/{application_id}/policy-discoveries` allows exact discovery by policy code + identifier and stores a 60-minute Redis authorization for the discovered policy.

## Target
- Workitem: `2026-06-01_tpaclaims-selfcare-policy-discoveries`
- Parent: `2026-05-27_tpaclaims-capability-code-standardization`
- Repository: `habit-tpaclaims-pyservice-layer`
- Repo path: `habit-tpaclaims-pyservice-layer/`
- Branch: `feature/tpaclaims-legacy-selfcare-access`
- Profile: governed
- Recommended agent: `backend-implementer`, full mode

## Current problem
`GET /v3/tpa-claims/applications/{application_id}/policies` currently uses the legacy/selfcare effective distributor scope. That scope may include sibling distributors when the user has parent-distributor permissions.

This diverges from the legacy platform behavior. In the legacy platform, sibling-distributor policies are not broadly listed. They are only discoverable through the dedicated endpoint:

```http
POST /v3/applications/{application_id}/extended-policies-search
```

with:

```json
{
  "code": "POLICY-CODE",
  "identifier": "DOCUMENT-NUMBER"
}
```

The TPA Claims BE must preserve that model.

## Required endpoint
Create:

```http
POST /v3/tpa-claims/applications/{application_id}/policy-discoveries
Content-Type: application/json

{
  "code": "POLICY-CODE",
  "identifier": "DOCUMENT-NUMBER"
}
```

Recommended endpoint file:

```text
tpaclaims/views/collections/tpaclaims_applications_policy_discoveries.py
```

Rationale: this creates a temporary policy discovery resource. Keep one endpoint per file and follow the normalized endpoint filename convention.

## Response contract
Return a JSON-safe object with:

```json
{
  "discovery_id": "opaque-id",
  "expires_at": "2026-06-01T12:00:00Z",
  "ttl_seconds": 3600,
  "policy": {
    "...": "same safe policy shape used by the application policy list"
  }
}
```

Use the same serializer shape as the application-scoped policy list where practical, so the FE can select the discovered policy without a second contract.

## Access rules
For legacy/selfcare operators:

- Normal policy list:
  - must use direct selfcare scope only;
  - must not include sibling distributors from `io.habit.access.discoverable.parent_distributor_sales.all`;
  - must not include sibling distributors from `io.habit.access.visible.parent_distributor_sales.all`.

- Policy discovery:
  - requires resolved legacy/selfcare visibility for the application;
  - requires exact `code`;
  - requires exact `identifier`;
  - may search direct distributors;
  - may search sibling distributors only when the user has `io.habit.access.discoverable.parent_distributor_sales.all`, matching the legacy endpoint behavior;
  - must fail closed when the selfcare context cannot be resolved.

For normal TPA operators:

- Preserve current TPA access behavior.
- Do not weaken local role/capability/scope enforcement.
- A TPA admin/global operator may use existing scoped policy list/filter behavior as before.

## Redis discovery authorization
On a successful legacy/selfcare discovery, write a Redis key with TTL `3600`.

Suggested key shape:

```text
TPA/selfcare-policy-discoveries/{platform_user_id}/{application_id}/{policy_id}
```

Suggested value:

```json
{
  "discovery_id": "opaque-id",
  "platform_user_id": "...",
  "application_id": "...",
  "policy_id": "...",
  "policy_code": "...",
  "identifier_hash": "sha256-or-md5-of-normalized-identifier",
  "distributor_id": "...",
  "created_at": "...",
  "expires_at": "..."
}
```

Do not store the raw document number unless existing project conventions require it. A stable hash is enough for audit/debug.

## Follow-up policy access
After discovery, the existing policy-scoped claim-opening endpoints should work without adding a new request parameter, as long as the Redis discovery authorization is still valid:

```http
GET  /v3/tpa-claims/applications/{application_id}/policies/{policy_id}/claim-journey-specs
POST /v3/tpa-claims/applications/{application_id}/policies/{policy_id}/claims
POST /v3/tpa-claims/applications/{application_id}/policies/{policy_id}/claims/{claim_id}/submit
```

Those endpoints should allow a legacy/selfcare policy if either:

- the policy is in the user's direct selfcare scope; or
- a valid Redis discovery authorization exists for `platform_user_id + application_id + policy_id`.

If the Redis discovery has expired and the policy is not in direct scope, fail closed with a clear 403 error such as `tpa_policy_discovery_expired_or_missing`. The FE will ask the operator to repeat the exact search.

## Important implementation notes
- Do not let broad list/search endpoints become a sibling-distributor discovery channel.
- Do not make `visible.parent_distributor_sales.all` broaden the normal policy list.
- Do not use sibling expansion for claim queue visibility. Existing claim queue rules still apply: direct access or claims created by the current platform user.
- Keep the existing Redis rematerialization behavior from the previous child.
- Keep repeated `workspace_status` OR semantics from the previous child.

## Tests expected
Add focused BE tests for:

- legacy/selfcare `/applications/{application_id}/policies` does not return sibling-distributor policies.
- `POST /policy-discoveries` returns a sibling policy only with `io.habit.access.discoverable.parent_distributor_sales.all` and exact `code + identifier`.
- `POST /policy-discoveries` denies missing code or identifier.
- `POST /policy-discoveries` denies sibling policy without discoverable permission.
- discovery writes Redis with TTL `3600`.
- policy claim journey/create/submit allow discovered sibling policy while Redis key exists.
- policy claim journey/create/submit deny discovered sibling policy after Redis key is missing/expired.
- direct-scope policies still work without Redis discovery.
- TPA operator behavior is unchanged.

At minimum rerun the existing application policies, policy opening, legacy selfcare, and application claims test groups touched by this work.

## Artifact expectations
Create:

- `.ai/workitems/2026-06-01_tpaclaims-selfcare-policy-discoveries/artifacts/implementation_notes.md`
- `.ai/workitems/2026-06-01_tpaclaims-selfcare-policy-discoveries/artifacts/test_report.md` if test output is substantial or partial

Each terminal or decision-bearing artifact must include:

```md
## Final decision
```

When complete:

- set `state.json.status = implemented_pending_review`
- set `state.json.next_action = backend reviewer review selfcare policy discoveries`
- set `state.json.canonical_artifact = .ai/workitems/2026-06-01_tpaclaims-selfcare-policy-discoveries/artifacts/implementation_notes.md`
- update `.ai/index.json` if tracked.
