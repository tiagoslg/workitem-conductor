# Frontend Review — 2026-05-27_tpaclaims-application-policy-search-contract

**Repository:** `habit-agent-configurator-hub`  
**Branch:** `feature/tpaclaims-operator-policy-claim-opening`

---

## Review history

### Round 1 — 2026-05-27 — Commit `ab4678c`
**Result: Changes Requested**

Contract migration had not been applied. The branch contained only the initial feature commit. `searchOperatorPolicies` was still calling `tpa-claims/policies` with a `q` param. No application context, no structured filters, no search gating.

Blocking items issued to the implementer:
1. Add `PolicySearchFilters`, replace `searchOperatorPolicies` signature, call `tpa-claims/applications/{applicationId}/policies`, use `qs.append` for `state`, remove `q`.
2. Add `usePersistedApplication` + `ApplicationSelect`, gate query on `applicationId`, replace single search input with structured filter fields, update query key to include `applicationId`, clear policy on application change.

---

### Round 2 — 2026-05-27 — Commit `42493eb`
**Result: Approved with comments**

All blocking items from Round 1 resolved. Build and lint pass.

---

## Contract verification

- **New endpoint:** ✅ Calls `tpa-claims/applications/${encodeURIComponent(applicationId)}/policies`. Path correct, ID URL-encoded.
- **Legacy endpoint removed:** ✅ `tpa-claims/policies` no longer present in `searchOperatorPolicies`.
- **Query params:** ✅ All six structured filters serialized — `code`, `distributor_id`, `name`, `email`, `phone`, `document_number`. Each trimmed before appending.
- **Repeated state handling:** ✅ `qs.append("state", s)` inside `for...of`. Handles `string | string[]` via normalization. Correct.
- **No `q` usage:** ✅ Confirmed absent.
- **Response type:** ✅ `unwrapElements` handles `T[]` and `{ elements: T[] }`. `OperatorPolicy` has no `code`/`remote_code` — correct per backend non-blocking note.

---

## Application context review

- **Application selector/context:** ✅ `ApplicationSelect` rendered with `token`, `value={applicationId}`, `onChange`. Uses standardized shared component.
- **Search gating:** ✅ `enabled: !!token && !!applicationId`. `applicationId` defaults to `""` (falsy) — gate works correctly.
- **Policy clearing on application change:** ✅ `onChange` calls `setApplicationId(id)` and `setSelectedPolicy(null)`.
- **Stale state risks:** ✅ Query key is `["operator-policies", applicationId, filters]` — fully scoped. No cross-application cache pollution.

---

## Flow preservation

- **Policy selection:** ✅ `holderLabel` fallback chain intact. `setDraftClaimId(null)` on selection.
- **Draft/open claim:** ✅ `createPolicyClaimDraft` flow unchanged and correct.
- **Continue/submit:** ✅ `submitPolicyClaimDeclaration` → invalidate → `navigate(/claims/${claimId})` intact.

---

## Type/API safety

- **Auth:** ✅ `token` passed via `fetchJson` helper throughout.
- **API base helpers:** ✅ `fetchJson` from `@/lib/http` used exclusively. No raw fetch or mocks.
- **Missing backend fields:** ✅ `OperatorPolicy` has no `code`/`remote_code`. Safe.
- **Policyholder nullability:** ✅ `policyholder` typed `| null`, accessed with optional chaining.

---

## Validation

- **Commands run:** `npm run build`, `npm run lint`
- **Results:** Both pass clean. Chunk size warning (`> 500 kB`) is pre-existing.

---

## Required changes
None.

---

## Non-blocking comments

- `limit` defaults to `25`. No FE enforcement of the backend cap (`50`). Low risk with current UI, but consider `Math.min(filters.limit ?? 25, 50)` in the service if limit becomes user-configurable.
- `draftClaimId` is not explicitly reset when `applicationId` changes (only when a new policy is selected). No functional bug since `selectedPolicy` is cleared first, but a `useEffect` would make the intent explicit.
- Structured filter inputs are reactive per keystroke. The explicit "Search" button mitigates unintended fetches. Acceptable as-is.
- `unwrapElements` discards `size`. Thread through if pagination is planned.

---

## Final decision

**Approved with comments.** Contract migration is complete, application scoping is correct, legacy endpoint is gone, build and lint pass. Ready for backend+frontend smoke testing.
