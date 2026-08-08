# Security Hardening (v3.1)

## Controls

| Area | Implementation |
|------|----------------|
| AuthZ / IDOR | `AuthManager.authorize_resource()` ownership checks |
| Sessions | Token TTL (12h), idle timeout (30m), revocation list |
| Login abuse | Progressive lockout + rate limit on `/auth/login` |
| Input | `sanitize_object`, `validate_body_size` |
| Uploads | Extension allowlist, 10MB cap, path traversal block |
| Secrets | Encrypted `CredentialStore` + `rotate_key()` |
| Audit | login fail/lock/rate_limit, logout, upload_denied |

## Production

1. Set `PEAR_SECRET_KEY` to a long random value
2. Change default `admin`/`demo` passwords
3. Terminate TLS at reverse proxy (nginx/Caddy)
4. `PEAR_PROFILE=production`
5. Restrict CORS — see [CORS.md](CORS.md)

### Example nginx snippet

```nginx
server {
  listen 443 ssl;
  ssl_certificate     /etc/ssl/pear.crt;
  ssl_certificate_key /etc/ssl/pear.key;
  location / {
    proxy_pass http://127.0.0.1:8080;
    proxy_set_header Authorization $http_authorization;
    client_max_body_size 10m;
  }
}
```
