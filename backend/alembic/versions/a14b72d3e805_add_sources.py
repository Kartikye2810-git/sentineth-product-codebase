"""Stable source identities above document payloads; backfill existing PDFs."""
from alembic import op
import sqlalchemy as sa

revision = 'a14b72d3e805'
down_revision = 'f93a61c2d704'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('sources',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('organization_id', sa.Uuid(), sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('origin', sa.String(50), nullable=False),
        sa.Column('namespace', sa.String(255), nullable=False),
        sa.Column('external_id', sa.String(500), nullable=False),
        sa.Column('uri', sa.Text(), nullable=True),
        sa.Column('sync_state', sa.String(20), nullable=False),
        sa.Column('error_code', sa.String(50), nullable=True),
        sa.Column('created_by_user_id', sa.Uuid(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('last_synced_at', sa.DateTime(), nullable=True),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.UniqueConstraint('id', 'organization_id', name='uq_source_tenant'),
        sa.UniqueConstraint('organization_id', 'origin', 'namespace', 'external_id', name='uq_source_origin'),
        sa.CheckConstraint("sync_state IN ('PENDING','PROCESSING','SYNCED','FAILED','DELETING','DELETED')", name='source_sync_state'),
    )
    op.create_index('ix_sources_organization_id', 'sources', ['organization_id'])
    op.add_column('documents', sa.Column('source_id', sa.Uuid(), nullable=True))
    # Reuse document IDs only for backfill, avoiding extensions or regenerated
    # document/chunk/vector IDs. Existing indexed data requires no re-embedding.
    op.execute("""INSERT INTO sources
        (id, organization_id, origin, namespace, external_id, uri, sync_state,
         error_code, created_by_user_id, created_at, updated_at, last_synced_at)
        SELECT id, organization_id, 'upload', '', CAST(id AS TEXT),
         '/organizations/' || CAST(organization_id AS TEXT) || '/documents/' || CAST(id AS TEXT),
         CASE status WHEN 'READY' THEN 'SYNCED' WHEN 'FAILED' THEN 'FAILED'
          WHEN 'PROCESSING' THEN 'PROCESSING' WHEN 'DELETING' THEN 'DELETING' ELSE 'PENDING' END,
         error_code, created_by_user_id, created_at, updated_at,
         CASE WHEN status = 'READY' THEN updated_at ELSE NULL END
        FROM documents""")
    op.execute('UPDATE documents SET source_id = id')
    op.alter_column('documents', 'source_id', nullable=False)
    op.create_unique_constraint('uq_documents_source_id', 'documents', ['source_id'])
    op.create_foreign_key('fk_document_source_tenant', 'documents', 'sources',
                          ['source_id', 'organization_id'], ['id', 'organization_id'])


def downgrade():
    op.drop_constraint('fk_document_source_tenant', 'documents', type_='foreignkey')
    op.drop_constraint('uq_documents_source_id', 'documents', type_='unique')
    op.drop_column('documents', 'source_id')
    op.drop_table('sources')
