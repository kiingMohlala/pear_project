# Deployment Guide (v3.00)

## Docker

```bash
docker compose up --build -d
curl http://localhost:8080/health
```

Environment:

| Variable | Purpose |
|----------|---------|
| `PEAR_PROFILE` | development / testing / production |
| `PEAR_HOST` / `PEAR_PORT` | bind address |
| `PEAR_DATA` | persistent data root |
| `PEAR_LOG_LEVEL` | DEBUG/INFO/WARNING |

## Production checklist

- [ ] `PEAR_PROFILE=production`
- [ ] Change default `admin`/`demo` passwords
- [ ] Restrict CORS (`cors_origins`)
- [ ] Mount durable volume on `/data`
- [ ] Enable backups (`scripts/admin_cli.py backup`)
- [ ] Configure reverse proxy TLS
- [ ] Run `python scripts/admin_cli.py integrity`

## Data migration

```bash
python -c "from pathlib import Path; from core.version import migrate_data_dir; print(migrate_data_dir(Path('/data')))"
```

## Health endpoints

- `GET /health` — liveness
- `GET /ready` — readiness
- `GET /metrics` — counters + sessions
