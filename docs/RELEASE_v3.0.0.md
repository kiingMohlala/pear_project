# PEAR v3.0.0

**Status:** Stable

## Highlights
- Multi-agent platform with planner, goals, workers, collaboration, learning
- Multi-user service API + dashboard
- Ops hardening: config profiles, audit, rate limits, backup/restore
- Optional n8n connector; offline-safe defaults

## Validation
See `docs/VALIDATION_REPORT_v3.md`. Regression, E2E, and stress runs passed with zero filed defects in the offline validation harness.

## Upgrade
```bash
python -c "from pathlib import Path; from core.version import migrate_data_dir; print(migrate_data_dir(Path('$PEAR_DATA')))"
```

## Security
Rotate default `admin`/`demo` credentials before internet exposure. Terminate TLS at the reverse proxy.
