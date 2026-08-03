# Tutorial: Your first connector

1. Subclass `connectors.base.Connector`
2. Implement `connect`, `authenticate`, `execute`
3. Register in `build_default_connectors()`
4. Keep secrets in CredentialStore
5. Stay disabled until credentials are provided

See scaffolds: `slack_connector.py`, `notion_connector.py`, `gdrive_connector.py`, `jira_connector.py`.
