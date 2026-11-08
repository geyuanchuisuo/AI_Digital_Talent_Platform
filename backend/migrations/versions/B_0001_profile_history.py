"""Profile history, evidence, edits, source pages and parse attempts.

Revision ID: B_0001_profile_history
Revises: D_0008_workflow_models
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = 'B_0001_profile_history'
down_revision = 'D_0008_workflow_models'
branch_labels = None
depends_on = None


def _id(name='id', *, primary=False):
    return sa.Column(name, mysql.BIGINT(unsigned=True), primary_key=primary, autoincrement=primary, nullable=False)


def _created():
    return sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP'))


def _create_table(name, *columns, **options):
    if not op.get_context().as_sql and sa.inspect(op.get_bind()).has_table(name):
        inspector = sa.inspect(op.get_bind())
        expected = {c.name: c for c in columns if isinstance(c, sa.Column)}
        actual = {c['name']: c for c in inspector.get_columns(name)}
        if set(expected) != set(actual):
            raise RuntimeError(f'Incompatible existing B table: {name}')
        for key, column in expected.items():
            found = actual[key]
            attributes = ('length', 'precision', 'scale', 'unsigned')
            if (column.nullable != found['nullable'] or column.type._type_affinity != found['type']._type_affinity
                or any(getattr(column.type, attr, None) != getattr(found['type'], attr, None)
                       for attr in attributes if attr != 'unsigned')
                or bool(getattr(column.type, 'unsigned', False)) != bool(getattr(found['type'], 'unsigned', False))):
                raise RuntimeError(f'Incompatible existing B column: {name}.{key}')
        unique_sets = {tuple(item['column_names']) for item in inspector.get_unique_constraints(name)}
        for constraint in columns:
            if isinstance(constraint, sa.UniqueConstraint) and tuple(constraint.columns.keys()) not in unique_sets:
                # Standalone constraints are attached only once create_table constructs a Table.
                names = tuple(str(item) for item in constraint._pending_colargs)
                if names not in unique_sets:
                    raise RuntimeError(f'Missing B unique constraint: {name}.{constraint.name}')
        return
    op.create_table(name, *columns, **options)


def _create_index(name, table, columns):
    if not op.get_context().as_sql:
        indexes = {item['name'] for item in sa.inspect(op.get_bind()).get_indexes(table)}
        if name in indexes:
            return
    op.create_index(name, table, columns)


def upgrade():
    options = dict(mysql_engine='InnoDB', mysql_charset='utf8mb4', mysql_collate='utf8mb4_unicode_ci')
    _create_table('talent_profile_versions', _id(primary=True), _id('talent_profile_id'),
        sa.Column('version', sa.Integer(), nullable=False), sa.Column('change_type', sa.String(20), nullable=False),
        sa.Column('profile_snapshot_json', sa.JSON(), nullable=False),
        sa.Column('changed_by', mysql.BIGINT(unsigned=True), nullable=True), _created(),
        sa.UniqueConstraint('talent_profile_id', 'version', name='uk_profile_version'), **options)
    _create_index('ix_talent_profile_versions_talent_profile_id', 'talent_profile_versions', ['talent_profile_id'])
    _create_table('talent_field_evidence', _id(primary=True), _id('talent_profile_id'),
        sa.Column('profile_version', sa.Integer(), nullable=False), sa.Column('field_name', sa.String(100), nullable=False),
        _id('resume_file_id'), sa.Column('source_page', sa.Integer(), nullable=False),
        sa.Column('source_text', sa.Text(), nullable=False), sa.Column('source', sa.String(20), nullable=False),
        sa.Column('confidence', sa.DECIMAL(4, 3), nullable=True),
        sa.Column('review_status', sa.String(20), nullable=False), _created(), **options)
    _create_index('idx_evidence_version', 'talent_field_evidence', ['talent_profile_id', 'profile_version'])
    _create_table('talent_profile_edits', _id(primary=True), _id('talent_profile_id'),
        sa.Column('profile_version', sa.Integer(), nullable=False), sa.Column('field_name', sa.String(100), nullable=False),
        sa.Column('old_value', sa.Text(), nullable=True), sa.Column('new_value', sa.Text(), nullable=True),
        _id('edited_by'), _created(), **options)
    _create_index('idx_edit_version', 'talent_profile_edits', ['talent_profile_id', 'profile_version'])
    _create_table('resume_pages', _id(primary=True), _id('resume_file_id'),
        sa.Column('page_no', sa.Integer(), nullable=False), sa.Column('text_content', sa.Text(), nullable=False),
        sa.Column('ocr_engine', sa.String(50), nullable=True), sa.Column('confidence', sa.DECIMAL(4, 3), nullable=True),
        _created(), sa.UniqueConstraint('resume_file_id', 'page_no', name='uk_page_file_no'), **options)
    _create_table('parse_attempts', _id(primary=True), _id('parse_task_id'),
        sa.Column('attempt_no', sa.Integer(), nullable=False), sa.Column('status', sa.String(20), nullable=False),
        sa.Column('stage', sa.String(20), nullable=True), sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=False), sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.UniqueConstraint('parse_task_id', 'attempt_no', name='uk_attempt_task_no'), **options)
    if op.get_context().as_sql:
        return
    # Capture existing data once, before any later mutation can overwrite baseline 1.
    from sqlalchemy.orm import Session
    from app.models import TalentProfile
    from app.modules.talents.history import save_version
    db = Session(bind=op.get_bind())
    try:
        last_id = 0
        while True:
            batch = db.scalars(sa.select(TalentProfile).where(TalentProfile.id > last_id).order_by(TalentProfile.id).limit(100)).all()
            if not batch:
                break
            for profile in batch:
                save_version(db, profile, 'baseline')
            last_id = batch[-1].id
        db.commit()
    finally:
        db.close()


def downgrade():
    for table in ('parse_attempts', 'resume_pages', 'talent_profile_edits', 'talent_field_evidence', 'talent_profile_versions'):
        op.drop_table(table)
