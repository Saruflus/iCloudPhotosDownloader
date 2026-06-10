"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-06-07

Creates the three core tables (downloaded_assets, download_jobs, schedules) and
the asset_status enum. Reflects plan decisions D6 (multi-file/version), D7/D8
(collision/fanout via the files JSON), and the asset-id-as-truth design.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Enum type managed explicitly (works in both online and offline --sql mode).
asset_status = postgresql.ENUM(
    "pending",
    "downloading",
    "completed",
    "failed",
    name="asset_status",
    create_type=False,
)


def upgrade() -> None:
    op.execute(
        "CREATE TYPE asset_status AS ENUM "
        "('pending', 'downloading', 'completed', 'failed')"
    )

    op.create_table(
        "downloaded_assets",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("asset_id", sa.String(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("media_type", sa.String(), nullable=True),
        sa.Column("is_live_photo", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("source_version", sa.String(), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("created_at_icloud", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("albums", postgresql.JSONB(), nullable=True),
        sa.Column("persons", postgresql.JSONB(), nullable=True),
        sa.Column("exif_data", postgresql.JSONB(), nullable=True),
        sa.Column("files", postgresql.JSONB(), nullable=True),
        sa.Column("original_path", sa.String(), nullable=True),
        sa.Column("status", asset_status, nullable=False, server_default="pending"),
        sa.Column("downloaded_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_verified_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_downloaded_assets_asset_id", "downloaded_assets", ["asset_id"], unique=True
    )
    op.create_index("ix_downloaded_assets_media_type", "downloaded_assets", ["media_type"])
    op.create_index(
        "ix_downloaded_assets_created_at_icloud", "downloaded_assets", ["created_at_icloud"]
    )
    op.create_index("ix_downloaded_assets_status", "downloaded_assets", ["status"])

    op.create_table(
        "download_jobs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("selected_albums", postgresql.JSONB(), nullable=True),
        sa.Column("selected_asset_ids", postgresql.JSONB(), nullable=True),
        sa.Column("folder_structure", postgresql.JSONB(), nullable=True),
        sa.Column("include_raw", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("include_jpeg", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("include_heic", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("include_video", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("download_version", sa.String(), nullable=False, server_default="edited"),
        sa.Column("album_fanout", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("force_redownload", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("total_assets", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("downloaded_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("celery_task_id", sa.String(), nullable=True),
    )
    op.create_index("ix_download_jobs_status", "download_jobs", ["status"])

    op.create_table(
        "schedules",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("cron_expression", sa.String(), nullable=False),
        sa.Column("job_config", postgresql.JSONB(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_run_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("schedules")
    op.drop_index("ix_download_jobs_status", table_name="download_jobs")
    op.drop_table("download_jobs")
    op.drop_index("ix_downloaded_assets_status", table_name="downloaded_assets")
    op.drop_index("ix_downloaded_assets_created_at_icloud", table_name="downloaded_assets")
    op.drop_index("ix_downloaded_assets_media_type", table_name="downloaded_assets")
    op.drop_index("ix_downloaded_assets_asset_id", table_name="downloaded_assets")
    op.drop_table("downloaded_assets")
    op.execute("DROP TYPE asset_status")
