"""create channel_secret_mappings table

Revision ID: 001_create_channel_secret_mappings
Revises: 
Create Date: 2026-09-10 17:30:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '001_create_channel_secret_mappings'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'channel_secret_mappings',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('channel_id', sa.String(length=255), nullable=False),
        sa.Column('channel_name', sa.String(length=255), nullable=True),
        sa.Column('provider', sa.String(length=50), nullable=False),
        sa.Column('aws_secret_name', sa.String(length=255), nullable=False),
        sa.Column('aws_secret_arn', sa.String(length=512), nullable=True),
        sa.Column('status', sa.String(length=50), server_default='active', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_by', sa.String(length=255), server_default='admin', nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('channel_id', 'provider', name='uq_channel_provider')
    )
    op.create_index('idx_channel_secret_lookup', 'channel_secret_mappings', ['channel_id'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_channel_secret_lookup', table_name='channel_secret_mappings')
    op.drop_table('channel_secret_mappings')
