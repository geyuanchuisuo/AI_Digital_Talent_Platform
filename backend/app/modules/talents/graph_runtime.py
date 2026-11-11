"""Opt-in client lifecycle; startup never exposes credentials or driver errors."""
from threading import Lock
from app.config import settings
from app.core.ai.graph_client import GraphClient,GraphUnavailable

_client=None
_lock=Lock()


def get_client():
    global _client
    with _lock:
        if not settings.neo4j_enabled:
            raise GraphUnavailable('Graph is disabled')
        if _client is None:
            candidate=GraphClient(enabled=settings.neo4j_enabled,uri=settings.neo4j_uri,
                username=settings.neo4j_username,password=settings.neo4j_password.get_secret_value(),
                database=settings.neo4j_database,namespace=settings.neo4j_namespace,timeout=settings.neo4j_timeout)
            try:
                candidate.prepare()
            except Exception:
                candidate.close()
                raise GraphUnavailable('Graph dependency is unavailable') from None
            _client=candidate
        return _client


def close_client():
    global _client
    with _lock:
        if _client is not None:
            _client.close()
            _client=None
