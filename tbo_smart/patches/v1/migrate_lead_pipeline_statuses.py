"""Move the existing leads to the pipeline statuses (docs/lead-pipeline.md §3). Runs on deploy,
whatever the switches say; idempotent, and reversible with
tbo_smart.lead_pipeline.status_migration.revert."""

from tbo_smart.finance import crm_installed


def execute():
	if not crm_installed():
		return
	# the patch runs before after_migrate, so it sets up the field and statuses it needs itself
	from tbo_smart.lead_pipeline.setup import setup_lead_pipeline
	from tbo_smart.lead_pipeline.status_migration import execute as migrate

	setup_lead_pipeline()
	counts = migrate()
	for (old, new), count in sorted(counts.items()):
		print(f"Lead pipeline: {count} leads {old} → {new}")
