from tbo_smart.finance import crm_installed
from tbo_smart.finance.backfill import run
from tbo_smart.finance.setup import create_finance_fields


def execute():
	# no CRM app, no deals to link to; if the CRM is installed later, run
	# `bench --site <site> execute tbo_smart.finance.backfill.run` once
	if not crm_installed():
		return
	# patches run before after_migrate, so make sure the crm_deal fields exist first
	create_finance_fields()
	run()
