# Frontend Implementation Notes

**Work item:** 2026-05-27_tpaclaims-application-policy-search-contract
**Stage:** fe_implementation
**Result:** Complete — build passes, legacy endpoint removed.

## Changes made

### `habit-agent-configurator-hub/src/services/policyClaimOpening.ts`
- Removed `searchOperatorPolicies(params: { q?, limit? }, token)` calling `tpa-claims/policies`
- Added `PolicySearchFilters` interface with structured fields: `state`, `code`, `distributor_id`, `name`, `email`, `phone`, `document_number`, `limit`
- Replaced with `searchOperatorPolicies(applicationId: string, filters: PolicySearchFilters, token)` calling `tpa-claims/applications/{applicationId}/policies`
- `state` param supports repeated values via `qs.append`

### `habit-agent-configurator-hub/src/pages/claims/PolicyClaimOpeningPage.tsx`
- Added `usePersistedApplication` hook — persists selected application across navigation
- Added `ApplicationSelect` component at the top of the Policy Search card
- Replaced single generic search input with 6 structured filter inputs: code, distributor_id, name, email, phone, document_number
- Policy search is gated: disabled and shows a prompt when no application is selected
- Changing application clears the selected policy to avoid cross-application inconsistency
- Query key updated to include `applicationId` and the full `filters` object

## Validation
- `npm run build` passes (✓ 2194 modules transformed)

## Final decision
Frontend consumer updated. Legacy `GET /v3/tpa-claims/policies` is no longer called from the Configurator Hub. All policy searches now use the application-scoped endpoint.
