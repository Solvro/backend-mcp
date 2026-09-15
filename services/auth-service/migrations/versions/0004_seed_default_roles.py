import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql, sqlite

revision = "0004_seed_default_roles"
down_revision = "0003_refresh_token_family"
branch_labels = None
depends_on = None

ROLES = (
    ("user", "Default role granted at registration"),
    ("admin", "Full administrative access"),
)

roles = sa.table("roles", sa.column("name", sa.String), sa.column("description", sa.String))


def upgrade() -> None:
    insert = postgresql.insert if op.get_bind().dialect.name == "postgresql" else sqlite.insert
    op.execute(
        insert(roles)
        .values([{"name": name, "description": desc} for name, desc in ROLES])
        .on_conflict_do_nothing(index_elements=["name"])
    )


def downgrade() -> None:
    op.execute(roles.delete().where(roles.c.name.in_([name for name, _ in ROLES])))
