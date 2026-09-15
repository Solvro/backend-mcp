import sqlalchemy as sa
from alembic import op

revision = "0003_refresh_token_family"
down_revision = "0002_create_auth_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("refresh_tokens") as batch:
        batch.add_column(sa.Column("family_id", sa.String(length=255), nullable=True))
    op.execute("UPDATE refresh_tokens SET family_id = jti")
    with op.batch_alter_table("refresh_tokens") as batch:
        batch.alter_column("family_id", nullable=False)
        batch.create_index("ix_refresh_tokens_family_id", ["family_id"])


def downgrade() -> None:
    with op.batch_alter_table("refresh_tokens") as batch:
        batch.drop_index("ix_refresh_tokens_family_id")
        batch.drop_column("family_id")
