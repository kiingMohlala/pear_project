# Security Review (v3.00-rc1)

## Authentication & authorization

- [x] Token-based auth on `/v1/*`
- [x] Roles: admin / user / api_client
- [x] Default users must be rotated in production
- [x] Rate limiting per user+path
- [x] Audit log for login success/failure

## Secrets

- [x] Connector credentials via CredentialStore (not agent code)
- [x] `.env` gitignored
- [x] Backup archives may contain sensitive data — protect backup_dir

## Plugins & connectors

- [x] Plugin permission-scoped API
- [x] Sensitive connector actions support approval flags
- [x] n8n optional and disabled by default

## Workers

- [x] Remote workers require explicit registration + token meta
- [x] Quarantine after repeated failures

## Residual risks

- Stdlib HTTP service is not a hardened reverse proxy — place behind TLS terminator
- Offline TTS/STT sidecars may write under workspace paths
- Demo credentials shipped for DX only
