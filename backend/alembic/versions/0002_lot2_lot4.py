"""Lot 2 + Lot 4 schema: job date-range, job type, asset↔job link, app settings.

Revision ID: 0002_lot2_lot4
Revises: 0001_initial
Create Date: 2026-06-12
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Date-range filter (Lot 2): only assets captured inside [date_from, date_to].
    op.add_column("download_jobs", sa.Column("date_from", sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column("download_jobs", sa.Column("date_to", sa.TIMESTAMP(timezone=True), nullable=True))
    # Verify/repair mode (Lot 4): 'download' (default) or 'verify'.
    op.add_column(
        "download_jobs",
        sa.Column("job_type", sa.String(), nullable=False, server_default="download"),
    )
    # Asset ↔ job link (Lot 4): the job that last touched the asset.
    op.add_column("downloaded_assets", sa.Column("last_job_id", sa.BigInteger(), nullable=True))
    op.create_index("ix_downloaded_assets_last_job_id", "downloaded_assets", ["last_job_id"])
    # Settings page (Lot 2): runtime-overridable settings, one row per key.
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("value", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
    op.drop_index("ix_downloaded_assets_last_job_id", table_name="downloaded_assets")
    op.drop_column("downloaded_assets", "last_job_id")
    op.drop_column("download_jobs", "job_type")
    op.drop_column("download_jobs", "date_to")
    op.drop_column("download_jobs", "date_from")
