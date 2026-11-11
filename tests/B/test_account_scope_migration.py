"""Migration semantics on isolated synthetic pre/post-column schemas."""
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text


@pytest.mark.parametrize("column_already_exists", [False, True])
def test_scope_migration_preserves_legacy_and_marks_all_existing_bindings(column_already_exists):
    path = Path(__file__).resolve().parents[2] / "backend/migrations/versions/A_0014_account_scope_mode.py"
    spec = importlib.util.spec_from_file_location("scope_mode_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        column = ", data_scope_mode VARCHAR(20) NOT NULL DEFAULT 'legacy'" if column_already_exists else ""
        connection.execute(text(f"CREATE TABLE users (id INTEGER PRIMARY KEY{column})"))
        connection.execute(text("CREATE TABLE sys_user_roles (user_id INTEGER, role_id INTEGER)"))
        connection.execute(text("INSERT INTO users (id) VALUES (1), (2), (3), (4)"))
        # No join with sys_roles: disabled/orphaned roles must also mark the account.
        connection.execute(text("INSERT INTO sys_user_roles VALUES (2, 22), (3, 999)"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            connection.execute(text("DELETE FROM sys_user_roles WHERE user_id = 2"))
            migration.upgrade()
        rows = connection.execute(text("SELECT id, data_scope_mode FROM users ORDER BY id")).all()
        assert rows == [(1, "legacy"), (2, "scoped"), (3, "scoped"), (4, "legacy")]
    engine.dispose()
