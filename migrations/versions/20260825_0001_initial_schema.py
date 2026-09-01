"""Create the initial availability schema."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260825_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cron_job_state",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("last_run", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.String(length=50), nullable=True),
        sa.Column("updated_at", sa.String(length=50), nullable=True),
        sa.PrimaryKeyConstraint("name"),
    )
    op.create_table(
        "cron_job_status",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("instance_id", sa.String(length=100), nullable=True),
        sa.Column("started_at", sa.String(length=50), nullable=True),
        sa.Column("updated_at", sa.String(length=50), nullable=True),
        sa.PrimaryKeyConstraint("name"),
    )
    op.create_table(
        "cron_job_execution_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_name", sa.String(length=255), nullable=True),
        sa.Column("instance_id", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("started_at", sa.String(length=50), nullable=True),
        sa.Column("completed_at", sa.String(length=50), nullable=True),
        sa.Column("duration", sa.Float(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(length=50), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_cron_job_execution_log_job_name",
        "cron_job_execution_log",
        ["job_name"],
        unique=False,
    )
    op.create_table(
        "collection_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_collection_runs_scheduled_at", "collection_runs", ["scheduled_at"], unique=True
    )
    op.create_table(
        "server_types",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("hardware_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("commercial_range", sa.String(length=80), nullable=False),
        sa.Column("total_cores", sa.Integer(), nullable=False),
        sa.Column("total_threads", sa.Integer(), nullable=False),
        sa.Column("ram_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_types", sa.String(length=160), nullable=False),
        sa.Column("has_gpu", sa.Boolean(), nullable=False),
        sa.Column("public_bandwidth_bps", sa.Integer(), nullable=False),
        sa.Column("specs_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "hardware_fingerprint", name="uq_server_type_identity"),
    )
    op.create_index("ix_server_types_name", "server_types", ["name"], unique=False)
    op.create_index("ix_server_types_has_gpu", "server_types", ["has_gpu"], unique=False)
    op.create_index(
        "ix_server_type_filters",
        "server_types",
        ["commercial_range", "total_cores", "ram_bytes"],
        unique=False,
    )
    op.create_index(
        "ix_server_types_commercial_range", "server_types", ["commercial_range"], unique=False
    )
    op.create_table(
        "offer_locations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("server_type_id", sa.Integer(), nullable=False),
        sa.Column("api_offer_id", sa.String(length=80), nullable=False),
        sa.Column("zone", sa.String(length=32), nullable=False),
        sa.Column("region", sa.String(length=32), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("current_stock", sa.String(length=32), nullable=False),
        sa.Column("hourly_currency", sa.String(length=8), nullable=True),
        sa.Column("hourly_price_nanos", sa.Integer(), nullable=True),
        sa.Column("monthly_currency", sa.String(length=8), nullable=True),
        sa.Column("monthly_price_nanos", sa.Integer(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["server_type_id"], ["server_types.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "api_offer_id", "zone", "server_type_id", name="uq_offer_location_api_zone_type"
        ),
    )
    op.create_index("ix_offer_locations_zone", "offer_locations", ["zone"], unique=False)
    op.create_index("ix_offer_locations_region", "offer_locations", ["region"], unique=False)
    op.create_index("ix_offer_locations_active", "offer_locations", ["active"], unique=False)
    op.create_index(
        "ix_offer_location_current", "offer_locations", ["region", "zone", "active"], unique=False
    )
    op.create_table(
        "zone_collection_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("collection_run_id", sa.Integer(), nullable=False),
        sa.Column("zone", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("offer_count", sa.Integer(), nullable=False),
        sa.Column("latency_seconds", sa.Float(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["collection_run_id"], ["collection_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("collection_run_id", "zone", name="uq_zone_run"),
    )
    op.create_index("ix_zone_collection_runs_zone", "zone_collection_runs", ["zone"], unique=False)
    op.create_table(
        "availability_observations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("collection_run_id", sa.Integer(), nullable=False),
        sa.Column("offer_location_id", sa.Integer(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stock", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("is_available", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["collection_run_id"], ["collection_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["offer_location_id"], ["offer_locations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "collection_run_id", "offer_location_id", name="uq_offer_observation_run"
        ),
    )
    op.create_index(
        "ix_availability_observations_observed_at",
        "availability_observations",
        ["observed_at"],
        unique=False,
    )
    op.create_index(
        "ix_availability_observations_is_available",
        "availability_observations",
        ["is_available"],
        unique=False,
    )
    op.create_index(
        "ix_offer_observation_time",
        "availability_observations",
        ["observed_at", "is_available"],
        unique=False,
    )
    op.create_table(
        "regional_observations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("collection_run_id", sa.Integer(), nullable=False),
        sa.Column("server_type_id", sa.Integer(), nullable=False),
        sa.Column("region", sa.String(length=32), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.ForeignKeyConstraint(["collection_run_id"], ["collection_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["server_type_id"], ["server_types.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "collection_run_id", "server_type_id", "region", name="uq_regional_observation_run"
        ),
    )
    op.create_index(
        "ix_regional_observations_region", "regional_observations", ["region"], unique=False
    )
    op.create_index(
        "ix_regional_observations_observed_at",
        "regional_observations",
        ["observed_at"],
        unique=False,
    )
    op.create_index(
        "ix_regional_observations_state", "regional_observations", ["state"], unique=False
    )
    op.create_index(
        "ix_regional_observation_stats",
        "regional_observations",
        ["server_type_id", "region", "observed_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("regional_observations")
    op.drop_table("availability_observations")
    op.drop_table("zone_collection_runs")
    op.drop_table("offer_locations")
    op.drop_table("server_types")
    op.drop_table("collection_runs")
    op.drop_index("ix_cron_job_execution_log_job_name", table_name="cron_job_execution_log")
    op.drop_table("cron_job_execution_log")
    op.drop_table("cron_job_status")
    op.drop_table("cron_job_state")
