"""Human identity, scoped keys, immutable audit history and usage accounting."""
from alembic import op
import sqlalchemy as sa

revision = 'f93a61c2d704'
down_revision = 'e81d4a72c603'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ingestion_jobs", sa.Column("request_id", sa.String(128), nullable=True))
    op.create_table('users',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('email', sa.String(320), nullable=False, unique=True),
        sa.Column('password_hash', sa.Text(), nullable=False),
        sa.Column('disabled', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False))
    op.create_table('memberships',
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('users.id'), primary_key=True),
        sa.Column('organization_id', sa.Uuid(), sa.ForeignKey('organizations.id'), primary_key=True),
        sa.Column('role', sa.String(20), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint("role IN ('owner','member','viewer')", name='membership_role'))
    op.create_table('user_sessions',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('token_hash', sa.String(64), nullable=False, unique=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('revoked_at', sa.DateTime(), nullable=True))
    op.create_index('ix_user_sessions_user_id', 'user_sessions', ['user_id'])
    op.create_table('invitations',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('organization_id', sa.Uuid(), sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('email', sa.String(320), nullable=False),
        sa.Column('role', sa.String(20), nullable=False),
        sa.Column('token_hash', sa.String(64), nullable=False, unique=True),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('accepted_at', sa.DateTime(), nullable=True),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.Uuid(), sa.ForeignKey('users.id'), nullable=False),
        sa.CheckConstraint("role IN ('owner','member','viewer')", name='invitation_role'))
    op.create_index('ix_invitations_organization_id', 'invitations', ['organization_id'])
    op.create_table('auth_throttles',
        sa.Column('bucket', sa.String(100), primary_key=True),
        sa.Column('requests', sa.Integer(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False))
    op.create_index('ix_auth_throttles_expires_at', 'auth_throttles', ['expires_at'])
    op.add_column('organization_api_keys', sa.Column('role', sa.String(20), nullable=False, server_default='owner'))
    op.add_column('organization_api_keys', sa.Column('label', sa.String(100), nullable=False, server_default='Legacy API key'))
    op.alter_column('organization_api_keys', 'role', server_default=None)
    op.alter_column('organization_api_keys', 'label', server_default=None)
    op.create_check_constraint('api_key_role', 'organization_api_keys', "role IN ('owner','member','viewer')")
    op.add_column('documents', sa.Column('created_by_user_id', sa.Uuid(), nullable=True))
    op.create_foreign_key('documents_created_by_user_id_fkey', 'documents', 'users', ['created_by_user_id'], ['id'])
    op.create_table('audit_events',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('organization_id', sa.Uuid(), nullable=True),
        sa.Column('actor_type', sa.String(20), nullable=False),
        sa.Column('actor_id', sa.Uuid(), nullable=True),
        sa.Column('action', sa.String(80), nullable=False),
        sa.Column('resource_id', sa.Uuid(), nullable=True),
        sa.Column('request_id', sa.String(128), nullable=True),
        sa.Column('details', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False))
    for column in ('organization_id', 'action', 'created_at'):
        op.create_index('ix_audit_events_' + column, 'audit_events', [column])
    op.create_table('usage_records',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('organization_id', sa.Uuid(), nullable=False),
        sa.Column('request_id', sa.String(128), nullable=True),
        sa.Column('model', sa.String(200), nullable=False),
        sa.Column('prompt_tokens', sa.Integer(), nullable=False),
        sa.Column('completion_tokens', sa.Integer(), nullable=False),
        sa.Column('reported_cost_usd', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False))
    for column in ('organization_id', 'created_at'):
        op.create_index('ix_usage_records_' + column, 'usage_records', [column])
    # Database enforcement covers bulk SQL too. A database administrator can
    # still alter triggers: separate runtime and migration credentials in prod.
    op.execute("""CREATE FUNCTION sentineth_audit_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Audit events are append-only'; END; $$""")
    op.execute("CREATE TRIGGER audit_no_changes BEFORE UPDATE OR DELETE OR TRUNCATE ON audit_events FOR EACH STATEMENT EXECUTE FUNCTION sentineth_audit_append_only()")


def downgrade():
    op.drop_column("ingestion_jobs", "request_id")
    op.execute('DROP TRIGGER audit_no_changes ON audit_events')
    op.execute('DROP FUNCTION sentineth_audit_append_only()')
    op.drop_table('usage_records')
    op.drop_table('audit_events')
    op.drop_column('documents', 'created_by_user_id')
    op.drop_constraint('api_key_role', 'organization_api_keys')
    op.drop_column('organization_api_keys', 'label')
    op.drop_column('organization_api_keys', 'role')
    for table in ('auth_throttles', 'invitations', 'user_sessions', 'memberships', 'users'):
        op.drop_table(table)
