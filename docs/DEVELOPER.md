# Developer Guide (v3.00)

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -c "from core.version import __version__; print(__version__)"
```

## Run CLI

```bash
python -m ui.app
```

## Run API

```bash
python -m service.app
# http://localhost:8080/dashboard
```

## Tests

```bash
python tests/test_basic.py
python tests/test_e2e_v300.py
python tests/test_perf_v300.py
```

## Add an agent

1. Subclass `agents.base.Agent` with `name`, `description`, `capabilities`.
2. Implement `_process(task)`.
3. Register in `ui/app.py` / session builder.
4. Prefer tools via Tool Registry; never call other agents directly.

## Add a connector

1. Subclass `connectors.base.Connector`.
2. Implement `connect`, `authenticate`, `execute`.
3. Register in `build_default_connectors()`.
4. Store secrets only in `CredentialStore`.

## Public API freeze (v3)

See `core/version.py` → `PUBLIC_APIS`. Breaking changes require a major version bump.
