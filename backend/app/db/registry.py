"""Register the complete ORM schema for Alembic, without importing HTTP routers."""

# ruff: noqa: F401
from app.ai import models as ai_models
from app.auth import models as auth_models
from app.conflicts import models as conflict_models
from app.documents import models as document_models
from app.governance import models as governance_models
from app.graph import models as graph_models
from app.operations import models as operation_models
from app.processing import models as processing_models
from app.rules import models as rule_models
from app.search import models as search_models
from app.workflows import models as workflow_models
