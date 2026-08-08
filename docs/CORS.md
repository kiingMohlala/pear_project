# CORS Configuration

PEAR reads browser cross-origin policy from config key **`cors_origins`**.

## Profiles

| Profile | Default `cors_origins` | Meaning |
|---------|------------------------|---------|
| development | `["*"]` | Allow any origin (convenient local UI) |
| testing | inherits / open | Test-friendly |
| production | `[]` | **No** cross-origin browser access unless you set origins |

## Configuration

**JSON config** (`config.json` or `config/production.example.json`):

```json
{
  "cors_origins": ["https://app.example.com", "https://admin.example.com"]
}
```

**Environment** (optional future): prefer editing config file or:

```bash
# production — set explicit origins only
export PEAR_PROFILE=production
```

Then in config:

```json
"cors_origins": ["https://your-frontend.example"]
```

## Behavior

| Setting | `Access-Control-Allow-Origin` |
|---------|-------------------------------|
| `["*"]` | `*` (no credentialed cross-origin) |
| `["https://app.example.com"]` | Echoes request `Origin` **only if listed**; sets `Vary: Origin` |
| `[]` | Header omitted → browser blocks cross-origin XHR |

Also sent on allowed responses:

- `Access-Control-Allow-Methods: GET, POST, PUT, PATCH, DELETE, OPTIONS`
- `Access-Control-Allow-Headers: Authorization, Content-Type, X-Requested-With, Accept, Origin`
- `Access-Control-Max-Age: 600`

## Preflight

`OPTIONS` requests receive the same CORS headers (stdlib server and FastAPI).

## Reverse proxy

If TLS terminates at nginx/Caddy, either:

1. Let PEAR emit CORS (set `cors_origins` to your real HTTPS frontends), or  
2. Handle CORS only at the proxy and keep PEAR `cors_origins: []`.

Do **not** combine `*` with cookies/credentials.

## Checklist

- [ ] Production uses explicit HTTPS origins (not `*`)
- [ ] Mobile apps calling the API **directly** (non-browser) ignore CORS
- [ ] Dashboard on the **same host** as the API does not need CORS
