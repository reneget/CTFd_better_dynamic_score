"""Create immutable values for solves."""

import sqlalchemy as sa

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade(op=None):
    inspector = sa.inspect(op.get_bind())
    if "fixed_dynamic_awarded_values" not in inspector.get_table_names():
        op.create_table(
            "fixed_dynamic_awarded_values",
            sa.Column("solve_id", sa.Integer(), nullable=False),
            sa.Column("awarded_value", sa.Integer(), nullable=False),
            sa.Column("created", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["solve_id"], ["solves.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("solve_id"),
        )


def downgrade(op=None):
    if "fixed_dynamic_awarded_values" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("fixed_dynamic_awarded_values")
