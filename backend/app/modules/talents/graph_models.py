"""B09 exports; definitions remain in the existing B model file to avoid cycles."""
from app.models.talent import GraphSource,GraphSyncJob

GRAPH_MODELS=(GraphSource,GraphSyncJob)
