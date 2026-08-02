from .base import Connector, ConnectorCapability, ConnectorResult, ConnectorStatus
from .credentials import CredentialStore
from .registry import ConnectorRegistry
from .local_files import LocalFilesConnector
from .email_connector import EmailConnector
from .calendar_connector import CalendarConnector
from .github_connector import GitHubConnector


def build_default_connectors(workspace=None) -> ConnectorRegistry:
    reg = ConnectorRegistry()
    reg.register(LocalFilesConnector(workspace=workspace))
    reg.register(EmailConnector())
    reg.register(CalendarConnector())
    reg.register(GitHubConnector())
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
    "build_default_connectors",
]
