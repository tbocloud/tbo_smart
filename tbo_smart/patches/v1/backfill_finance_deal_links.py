from tbo_smart.finance.backfill import run
from tbo_smart.finance.setup import create_finance_fields


def execute():
	# patches run before after_migrate, so make sure the crm_deal fields exist first
	create_finance_fields()
	run()
