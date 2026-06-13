# 2026-06-01_tpaclaims-selfcare-policy-discoveries

## Final decision
Implement a backend application-scoped policy discovery flow for legacy/selfcare operators, preserving the legacy platform behavior where sibling-distributor policies are not visible in the normal policy list and can only be accessed through an exact policy code + identifier discovery.

## Context
Parent workitem: `2026-05-27_tpaclaims-capability-code-standardization`

Repository: `habit-tpaclaims-pyservice-layer`

Branch: `feature/tpaclaims-legacy-selfcare-access`

Profile: governed

Legacy reference endpoint:

```http
POST /v3/applications/{application_id}/extended-policies-search
```

New TPA endpoint:

```http
POST /v3/tpa-claims/applications/{application_id}/policy-discoveries
```

Payload:

```json
{
  "code": "POLICY-CODE",
  "identifier": "DOCUMENT-NUMBER"
}
```

Expected Redis TTL for a successful discovery: `3600` seconds.
