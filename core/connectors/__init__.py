from .base import Connector, ConnectorCapability, ConnectorResult, ConnectorStatus
from .credentials import CredentialStore
from .registry import ConnectorRegistry
from .local_files import LocalFilesConnector
from .email_connector import EmailConnector
from .calendar_connector import CalendarConnector
from .github_connector import GitHubConnector
from .n8n_connector import N8NConnector
from .slack_connector import SlackConnector
from .notion_connector import NotionConnector
from .gdrive_connector import GoogleDriveConnector
from .jira_connector import JiraConnector
from .quant_connector import QuantConnector


def build_default_connectors(workspace=None) -> ConnectorRegistry:
    reg = ConnectorRegistry()
    reg.register(LocalFilesConnector(workspace=workspace))
    reg.register(EmailConnector())
    reg.register(CalendarConnector())
    reg.register(GitHubConnector())
    # n8n is optional and disabled until base_url is configured
    reg.register(N8NConnector())
    reg.register(SlackConnector())
    reg.register(NotionConnector())
    reg.register(GoogleDriveConnector())
    reg.register(JiraConnector())
    reg.register(QuantConnector())
    return reg


__all__ = [
    "Connector",
    "ConnectorCapability",
    "ConnectorResult",
    "ConnectorStatus",
    "CredentialStore",
    "ConnectorRegistry",
    "LocalFilesConnector",
    "EmailConnector",
    "CalendarConnector",
    "GitHubConnector",
    "N8NConnector",
    "JiraConnector",
    "GoogleDriveConnector",
    "NotionConnector",
    "SlackConnector",
    "build_default_connectors",
    "QuantConnector",
]
