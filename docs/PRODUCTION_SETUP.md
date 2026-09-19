# Production setup

## Release migrations

Cloud Build updates the migration job to the release image and waits for it to
succeed before updating API or UI services. A failed migration stops the release.
The build service account needs permission to update and execute the existing
Cloud Run job and act as its configured worker service account.

The worker applies SQL files in filename order under a PostgreSQL transaction and
advisory lock. A checksum ledger skips previously applied files and rejects edits
to applied migrations. Add new numbered migrations for subsequent schema changes.
The first ledger-enabled run safely replays the existing idempotent 001 and 002
files. Use backward-compatible schema changes because the previous API continues
serving while migrations run. Database backups and restoration remain operational
responsibilities; the release does not automatically reverse schema changes.

## Organization sign-in

Set `login_url`, `oidc_issuer`, `oidc_audience`, and `oidc_jwks_url` in the chosen
Terraform environment. Both deployed UIs explicitly use JWT mode and direct users
to the HTTPS organization gateway when no bearer token is present.

The organization gateway must authenticate users and forward their signed JWT in
the Authorization header, including on Streamlit WebSocket connections. Strip
client-supplied identity headers at the gateway. The API independently verifies
signature, issuer, audience, expiry, and the required claims: `sub`, `tenant_id`,
`iat`, and `exp`. `groups` must be an array of authorized group names. Configure
these claims in the identity provider; never let users choose their tenant or roles.

This application-level JWT is separate from Cloud Run IAM authentication. Keep
the existing service access restrictions and configure your gateway and service
transport to satisfy Cloud Run invocation authentication as well. Do not make
services public merely to work around an invocation error.

The two UIs route all outbound traffic through Direct VPC egress on the private
subnet, which has Private Google Access enabled. This permits calls to the API's
restricted `run.app` endpoint without opening public ingress. The API uses JWT
validation with its Cloud Run invoker IAM check disabled; the UI services retain
their existing IAM requirements. If future UI features need outbound public
internet access, configure an explicit NAT route rather than removing VPC egress.

Migration `003_trace_request_id.sql` adds server-generated request identifiers.
New dashboard activity groups by those identifiers; older records retain their
historical correlation-based grouping because their original request boundaries
cannot be reconstructed reliably.

The repository does not provision an organization identity provider or gateway.
Live activation requires the actual cloud project, identity application, gateway
URLs, secret versions, and an authenticated deployment account. No live migration,
Terraform apply, or identity-provider change has been performed by these changes.

Before enabling traffic, verify a real sign-in, token expiry, cross-tenant denial,
group-restricted documents, independent report approval, and both UI connections.
