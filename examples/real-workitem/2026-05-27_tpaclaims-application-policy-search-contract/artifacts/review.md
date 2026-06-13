# Backend Review — application-scoped policy search contract

**Work item:** 2026-05-27_tpaclaims-application-policy-search-contract
**Repository:** habit-tpaclaims-pyservice-layer
**Branch:** feature/tpaclaims-operator-policy-claim-opening
**Review type:** Contract-focused, pre-FE-handoff
**Reviewer:** backend-reviewer
**Date:** 2026-05-27

---

## Review result — pass 2 (2026-05-27)

**Approved with comments.** All blocking issues resolved. FE implementation may start.

---

## Review result — pass 1 (2026-05-27)

**Changes requested** — one blocking issue resolved in pass 2.

---

## Contract verification

### Endpoint
✅ **Correct.**
`GET /v3/tpa-claims/applications/{application_id:uuid}/policies` — view file, decorator, and class name all match the normalized naming convention.

### Authorization
✅ **Correct.**
`require_tpa_access(auth_context, required_capabilities=[CAP_POLICIES_SEARCH])` preserves the `policies.search` capability gate. `@auth_required(roles=["user"], validate_bo=True)` present.

### Application scoping
✅ **Correct.**
`WHERE cs.provider_id = %(application_id)s` is the first WHERE clause in the helper query. The `_application_policy_base_query` function does not rely on the legacy `quotes` join for scoping.

### Default state
✅ **Correct.**
View: `states = request.args.getlist("state") or None`
Helper: `effective_states = [s for s in (states or []) if s] or _ACTIVE_STATES`
`_ACTIVE_STATES = ["active"]` — only active policies returned when no `state` param supplied.

### Repeated state
✅ **Correct.**
`request.args.getlist("state")` is used, not `request.args.get("state")`.
State placeholders are expanded as `%(state_0)s, %(state_1)s, ...`. Tests confirm both `active` and `terminated` reach the query.

### distributor_id
✅ **Correct.**
Filter: `AND p.cdata->>'distributor_id' = %(distributor_id_filter)s`
SELECT: `p.cdata->>'distributor_id' AS distributor_id`
No reference to `q.distributor_id` or `quotes` in the new query.

### code / remote_code
✅ **Correct.**
`AND (p.code = %(code)s OR p.remote_code = %(code)s)` — exact equality, both columns, single shared param.

### Policyholder filters

| Filter | Required behaviour | Implementation | Verdict |
|--------|-------------------|----------------|---------|
| name | partial case-insensitive | `ip.data::text ILIKE %val%` against ALL ip rows | ⚠️ Broad (see non-blocking comment #1) |
| email | partial case-insensitive | `ip.data::text ILIKE %val%` against ALL ip rows | ⚠️ Broad (see non-blocking comment #1) |
| phone | suffix `LIKE '%{phone}'` | `ip.data::text LIKE %val` against ALL ip rows | ⚠️ Broad (see non-blocking comment #1) |
| document_number | **exact full-field** | `ip.data::text LIKE %val%` — **substring, no namespace filter** | ❌ **BLOCKING** |

### Response shape
✅ **Correct.**
Envelope: `{"elements": [...], "size": N}`
Each element: `id`, `policy_id`, `state`, `quote_id`, `clientservice_id`, `application_id`, `service_id`, `user_id`, `distributor_id`, `policyholder`
The `policyholder` key is present and populated by `_batch_load_policyholders_for_policies`, consistent with the existing policyholder read-model contract used in claim-case views.

---

## Required changes

### BLOCKING — document_number exact-field match is not implemented

**Location:** `tpaclaims/helpers/operator_policy_claim_actions.py` line 188

**Current:**
```python
if _clean_str(document_number):
    where_parts.append("AND ip.data::text LIKE %(ph_doc)s")
    params["ph_doc"] = f"%{_clean_str(document_number)}%"
```

**Problem:** Two independent failures:
1. Substring `LIKE %val%` is used instead of an exact-match. The contract requires exact full-field match.
2. No namespace filter is applied. The JOIN brings ALL `insureeproperties` rows for the insuree. A search for `document_number=12345` will match any row (phone, email, name, etc.) whose JSON text representation happens to contain "12345". Cross-field contamination is possible with realistic data.

**Required fix:**
```python
if _clean_str(document_number):
    where_parts.append(
        "AND EXISTS ("
        "  SELECT 1 FROM insureeproperties ip2"
        "  WHERE ip2.insuree_id = i.id"
        "  AND ip2.namespace IN ('document_number', 'tax_id', 'nif', 'vat_number', 'identity_number')"
        "  AND ("
        "    CASE WHEN jsonb_typeof(ip2.data) = 'object'"
        "    THEN COALESCE(ip2.data->>'value', ip2.data->>'text', ip2.data->>'label') = %(ph_doc)s"
        "    ELSE ip2.data::text = %(ph_doc)s"
        "    END"
        "  )"
        ")"
    )
    params["ph_doc"] = _clean_str(document_number)
```

**Test that must also be updated:**
`test_policyholder_document_number_filter` currently only asserts `"PT123456" in doc_val`, which passes with either substring or exact approach. Once the fix is in, this test should assert the exact value (no wildcards) AND that a namespace filter is present.

---

## Non-blocking comments

### 1. Cross-field contamination for name / email / phone

All three filters apply `ip.data::text ILIKE` (or `LIKE` for phone) against ALL `insureeproperties` rows because the JOIN is:
```sql
LEFT JOIN insureeproperties ip ON ip.insuree_id = i.id
```
with no namespace restriction.

With `DISTINCT ON (p.id)`, the query returns a policy if **any** property row matches. In practice:
- A name search for "Maria" is unlikely to accidentally match a phone or document row.
- A phone suffix search for "91234" could match a document_number or email address containing those digits.

This is architecturally imprecise and carries false-positive risk for dense or multi-value JSON data. The same EXISTS-subquery pattern used for the document_number fix above should eventually be applied to name, email, and phone with their respective namespace sets.

This is **safe enough for initial FE development** given the realistic data shapes and low contamination probability, but should be addressed before production readiness.

### 2. Missing `code` field in serialized response

The serialized policy object does not include `code` or `remote_code` fields even though the search filters on them. The FE will not be able to display the policy code without a follow-up single-policy fetch. This may be intentional, but if the policy list view is expected to show the policy code, it must be added to the serializer at line 200-213. Confirm product intent with FE.

### 3. `limit` default hard-coded to 25 in view, capped at 50 in helper

Both are reasonable. The FE should know the max cap is 50. This is acceptable but should appear in the API contract documentation for FE.

---

## Legacy endpoint removal check

✅ **Clean removal.**

| Check | Result |
|-------|--------|
| `tpaclaims/views/collections/tpaclaims_policies.py` deleted | ✅ Confirmed (commit `7233c28`) |
| `PoliciesCollection` import removed from `collections/__init__.py` | ✅ Confirmed |
| `search_operator_policies()` removed from helper | ✅ Confirmed |
| No test still expects the old flat-search endpoint | ✅ No references found |
| No backend code calls the removed helper | ✅ No references found |
| No other view still registers `/v3/tpa-claims/policies` as a search route | ✅ Remaining `/v3/tpa-claims/policies/{policy_id:uuid}/...` endpoints are policy-sub-resource routes with a required `policy_id` path param — these are unrelated and correct |

The removal commit message is accurate. The legacy helper `search_operator_policies()` was a global search with no scoping; its removal is architecturally clean.

Frontend impact is known and will be handled in the next implementation step (habit-agent-configurator-hub).

---

## Test coverage assessment

**Sufficient for the view layer and most helper contracts.**

| Area | Coverage | Gap |
|------|----------|-----|
| Endpoint path, auth role, capability | ✅ Tested | None |
| application_id scoping via `cs.provider_id` | ✅ Tested | None |
| Default active-only state | ✅ Tested | None |
| Repeated state params | ✅ Tested | None |
| `distributor_id` via `p.cdata` (not `q.distributor_id`) | ✅ Tested (x2) | None |
| code / remote_code exact match | ✅ Tested | None |
| name ILIKE partial | ✅ Tested | None |
| email ILIKE partial | ✅ Tested | None |
| phone suffix LIKE | ✅ Tested | None |
| document_number | ⚠️ Tested for substring only | Test must be updated when exact-match fix lands |
| Response envelope shape | ✅ Tested | None |
| Policyholder in response | ✅ Tested | None |
| Scope filter (non-global operator) | ✅ Tested | None |
| limit cap at 50 | ✅ Tested | None |

**Two additional tests required when blocking fix is merged:**
1. `test_document_number_exact_match_only` — assert that `pt_doc` param has no wildcards and that an EXISTS subquery with namespace filter appears in the SQL.
2. `test_document_number_no_cross_field_contamination` — assert that searching by a document_number value that would match another namespace (e.g., a phone row containing the same digits) does NOT return a false positive.

---

## Frontend handoff readiness

**Not yet ready.** One blocking change is required:

- **document_number filter must be corrected to exact-match with namespace scoping** before FE builds the search form and sends document_number queries.

Once that change is committed and the test updated:
- The response shape is stable and FE-safe.
- The `policyholder` block follows the existing contract.
- The `elements + size` envelope is consistent with other collection endpoints.
- The `limit` behaviour is predictable (default 25, max 50).
- No lifecycle change was introduced.

FE implementation (habit-agent-configurator-hub) should be sequenced **after** the document_number fix is committed to this branch.

---

## Final decision (pass 2)

**Approved with comments.**

Blocking issue resolved in commit `9a6ab24`:
- `document_number` filter replaced with namespace-scoped EXISTS subquery, exact equality match, no wildcards.
- `test_policyholder_document_number_filter` updated; two new tests added (`test_document_number_exact_match_only`, `test_document_number_no_cross_field_contamination`).
- 25 tests pass.

Remaining non-blocking items (cross-field contamination for name/email/phone, `code`/`remote_code` absent from serialized response, limit cap documentation) may be deferred to a follow-up iteration.

FE implementation in `habit-agent-configurator-hub` may start.
