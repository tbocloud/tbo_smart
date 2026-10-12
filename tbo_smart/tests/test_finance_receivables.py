from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate, nowdate

from tbo_smart.api.finance import exceptions as exceptions_api
from tbo_smart.api.finance import receivables as receivables_api
from tbo_smart.finance.digest import build_digest, send_digest
from tbo_smart.finance.reminders import ReminderRun, due_step, run_reminders
from tbo_smart.finance.setup import COLLECTIONS_VIEW, EMAIL_TEMPLATES
from tbo_smart.test_utils import (
	delete_crm_records,
	enable_erpnext_crm,
	enable_finance_receivables,
	get_collection_task,
	get_terms,
	make_deal_invoice,
	make_finance_deal,
	make_receipt,
	make_user,
	reset_finance_settings,
)

BDE = "rcv.bde@tbosmart.test"
OTHER_BDE = "rcv.bde2@tbosmart.test"
ACCOUNTS = "rcv.accounts@tbosmart.test"
APPROVER = "rcv.approver@tbosmart.test"
APPROVER_2 = "rcv.approver2@tbosmart.test"
HEAD = "rcv.head@tbosmart.test"
FINANCE_MANAGER = "rcv.fm@tbosmart.test"
NOTIFY = "tbo_smart.finance.reminders.enqueue_create_notification"


class ReceivablesTestCase(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		enable_erpnext_crm()
		make_user(BDE, ["Sales User"])
		make_user(OTHER_BDE, ["Sales User"])
		make_user(ACCOUNTS, ["Accounts User"])
		make_user(APPROVER, ["Accounts Manager", "TBO Finance Approver"])
		make_user(APPROVER_2, ["Accounts Manager", "TBO Finance Approver"])
		make_user(HEAD, ["Sales User"])
		make_user(FINANCE_MANAGER, ["Accounts Manager"])

	def setUp(self):
		frappe.set_user("Administrator")
		delete_crm_records((BDE, OTHER_BDE))
		reset_finance_settings()
		enable_finance_receivables(
			accounts_lead=ACCOUNTS, escalation_sales_head=HEAD, escalation_finance_manager=FINANCE_MANAGER
		)
		self.addCleanup(reset_finance_settings)
		self.addCleanup(frappe.set_user, "Administrator")
		notifier = patch(NOTIFY)
		self.notify = notifier.start()
		self.addCleanup(notifier.stop)

	def notified(self) -> set[str]:
		return {user for call in self.notify.call_args_list for user in call.args[0]}

	def run_for(self, invoice: str):
		return [row for row in ReminderRun().run() if row["invoice"] == invoice]


class TestReminderSteps(FrappeTestCase):
	def test_due_step(self):
		today = getdate("2026-10-12")
		self.assertIsNone(due_step(-4, None, None, today))
		self.assertEqual(due_step(-3, None, None, today), "D-3")
		self.assertIsNone(due_step(-1, "D-3", today, today))
		self.assertEqual(due_step(0, "D-3", None, today), "D0")
		# a missed week sends only the latest step
		self.assertEqual(due_step(9, None, None, today), "D+7")
		self.assertIsNone(due_step(12, "D+7", None, today))
		self.assertEqual(due_step(15, "D+7", None, today), "D+15")
		self.assertEqual(due_step(31, "D+15", None, today), "D+30")
		self.assertIsNone(due_step(33, "D+30", add_days(today, -3), today))
		self.assertEqual(due_step(37, "D+30", add_days(today, -7), today), "Weekly")
		self.assertIsNone(due_step(40, "Weekly", add_days(today, -3), today))
		self.assertEqual(due_step(44, "Weekly", add_days(today, -7), today), "Weekly")


class TestReminderRun(ReceivablesTestCase):
	def test_each_step_once(self):
		deal = make_finance_deal(BDE)
		soon = make_deal_invoice(deal, due_in=3)
		self.assertEqual([row["step"] for row in self.run_for(soon.name)], ["D-3"])
		self.assertEqual(self.run_for(soon.name), [])
		self.assertIsNone(get_collection_task(soon.name))

		today = make_deal_invoice(deal, due_in=0)
		self.assertEqual([row["step"] for row in self.run_for(today.name)], ["D0"])
		task = get_collection_task(today.name)
		self.assertEqual(
			(task.assigned_to, task.tbo_escalation_level, task.reference_docname), (ACCOUNTS, 1, deal)
		)

	def test_escalation_ladder(self):
		deal = make_finance_deal(BDE)
		frappe.db.set_value("CRM Deal", deal, "tbo_accounts_owner", APPROVER)
		late = make_deal_invoice(deal, due_in=-8)
		self.assertEqual([row["step"] for row in self.run_for(late.name)], ["D+7"])
		self.assertEqual(get_collection_task(late.name).tbo_escalation_level, 2)
		self.assertEqual(self.notified(), {APPROVER, BDE})

		later = make_deal_invoice(deal, due_in=-16)
		self.run_for(later.name)
		dunning = frappe.get_all(
			"Dunning", filters={"sales_invoice": later.name}, fields=["dunning_type", "docstatus"]
		)
		self.assertEqual(len(dunning), 1)
		self.assertTrue(dunning[0].dunning_type.startswith("Reminder 2"))
		self.assertEqual(dunning[0].docstatus, 0)
		self.assertIn(HEAD, self.notified())

		final = make_deal_invoice(deal, due_in=-31)
		self.run_for(final.name)
		types = frappe.get_all("Dunning", filters={"sales_invoice": final.name}, pluck="dunning_type")
		self.assertEqual([dunning_type.split(" - ")[0] for dunning_type in types], ["Final Notice"])
		self.assertIn(FINANCE_MANAGER, self.notified())
		self.assertEqual(get_collection_task(final.name).tbo_escalation_level, 4)

		[term] = get_terms(final.name)
		frappe.db.set_value("Payment Schedule", term.name, "tbo_last_reminder_on", add_days(nowdate(), -7))
		frappe.db.set_value("Sales Invoice", final.name, "due_date", add_days(nowdate(), -38))
		frappe.db.set_value("Payment Schedule", term.name, "due_date", add_days(nowdate(), -38))
		self.assertEqual([row["step"] for row in self.run_for(final.name)], ["Weekly"])

	def test_paid_invoice_and_switch_off(self):
		deal = make_finance_deal(BDE)
		paid = make_deal_invoice(deal, due_in=0)
		make_receipt("Sales Invoice", paid.name)
		self.assertEqual(self.run_for(paid.name), [])
		open_invoice = make_deal_invoice(deal, due_in=0)
		frappe.db.set_single_value("TBO Smart Settings", "enable_finance_receivables", 0)
		run_reminders()
		self.assertFalse(get_terms(open_invoice.name)[0].tbo_last_reminder)

	def test_client_email_only_when_switched_on(self):
		deal = make_finance_deal(BDE)
		invoice = make_deal_invoice(deal, due_in=3)
		frappe.db.set_value("Sales Invoice", invoice.name, "contact_email", "client@example.com")
		self.run_for(invoice.name)
		self.assertFalse(
			frappe.get_all(
				"Communication", filters={"reference_name": invoice.name, "subject": ("like", "Payment due%")}
			)
		)
		second = make_deal_invoice(deal, due_in=3)
		frappe.db.set_value("Sales Invoice", second.name, "contact_email", "client@example.com")
		enable_finance_receivables(send_client_reminders=1, accounts_lead=ACCOUNTS)
		with patch("frappe.core.doctype.communication.communication.Communication.send_email") as send:
			self.run_for(second.name)
		send.assert_called_once()
		communication = frappe.get_all(
			"Communication",
			filters={
				"reference_doctype": "Sales Invoice",
				"reference_name": second.name,
				"subject": ("like", "Payment due%"),
			},
			fields=["name", "recipients", "subject"],
		)
		self.assertEqual(len(communication), 1)
		self.assertIn("client@example.com", communication[0].recipients)
		self.assertIn(second.name, communication[0].subject)
		self.assertTrue(
			frappe.db.exists(
				"Communication Link",
				{"parent": communication[0].name, "link_doctype": "CRM Deal", "link_name": deal},
			)
		)


class TestExceptions(ReceivablesTestCase):
	def test_extension_maker_checker_and_effect(self):
		deal = make_finance_deal(BDE)
		invoice = make_deal_invoice(deal, due_in=-8)
		self.run_for(invoice.name)
		frappe.set_user(BDE)
		exception = exceptions_api.request_exception(
			invoice.name, "Extension request", "Client waits for budget", new_due_date=add_days(nowdate(), 10)
		)
		self.assertEqual(exception["status"], "Pending Approval")
		with self.assertRaises(frappe.PermissionError):
			exceptions_api.decide_exception(exception["name"], "approve")
		frappe.set_user(APPROVER)
		exceptions_api.decide_exception(exception["name"], "approve", "OK")
		self.assertEqual(
			frappe.db.get_value("TBO Finance Exception", exception["name"], "status"), "Approved"
		)
		self.assertFalse(get_terms(invoice.name)[0].tbo_last_reminder)
		frappe.set_user("Administrator")
		self.assertEqual(self.run_for(invoice.name), [])

	def test_requester_cannot_decide_and_rules(self):
		deal = make_finance_deal(BDE)
		invoice = make_deal_invoice(deal, due_in=-8)
		frappe.set_user(APPROVER)
		pause = exceptions_api.request_exception(
			invoice.name, "Client dispute", "Disputes scope", pause_until=add_days(nowdate(), 5)
		)
		with self.assertRaises(frappe.PermissionError):
			exceptions_api.decide_exception(pause["name"], "approve")
		frappe.set_user(APPROVER_2)
		with self.assertRaises(frappe.ValidationError):
			exceptions_api.decide_exception(pause["name"], "reject")
		exceptions_api.decide_exception(pause["name"], "approve")
		frappe.set_user("Administrator")
		self.assertEqual(self.run_for(invoice.name), [])

		frappe.set_user(BDE)
		waive = exceptions_api.request_exception(invoice.name, "Write-off request", "Goodwill")
		frappe.set_user(APPROVER)
		with self.assertRaises(frappe.ValidationError):
			exceptions_api.decide_exception(waive["name"], "approve")
		doc = frappe.get_doc("TBO Finance Exception", waive["name"])
		doc.status = "Approved"
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)

	def test_who_sees_exceptions(self):
		deal = make_finance_deal(BDE)
		invoice = make_deal_invoice(deal, due_in=-2)
		frappe.set_user(BDE)
		exception = exceptions_api.request_exception(
			invoice.name, "Pause request", "Holiday", pause_until=add_days(nowdate(), 3)
		)
		frappe.set_user(OTHER_BDE)
		self.assertNotIn(exception["name"], [row.name for row in exceptions_api.get_exceptions()["rows"]])
		with self.assertRaises(frappe.PermissionError):
			exceptions_api.request_exception(
				invoice.name, "Pause request", "x", pause_until=add_days(nowdate(), 3)
			)
		frappe.set_user(APPROVER_2)
		rows = {row.name: row for row in exceptions_api.get_exceptions()["rows"]}
		self.assertTrue(rows[exception["name"]].can_decide)


class TestReceivablesApi(ReceivablesTestCase):
	def test_ageing_scope_and_follow_up(self):
		deal = make_finance_deal(BDE)
		other = make_finance_deal(OTHER_BDE)
		make_deal_invoice(deal, rate=100, due_in=5)
		make_deal_invoice(deal, rate=200, due_in=-10)
		make_deal_invoice(deal, rate=300, due_in=-45)
		make_deal_invoice(other, rate=400, due_in=-100)
		frappe.set_user(BDE)
		data = receivables_api.get_receivables()
		self.assertEqual({row["crm_deal"] for row in data["rows"]}, {deal})
		[total] = data["totals"]
		self.assertEqual(
			total["buckets"], {"not_due": 100, "0_30": 200, "31_60": 300, "61_90": 0, "90_plus": 0}
		)
		self.assertEqual((total["outstanding"], total["overdue"]), (600, 500))

		overdue = next(row for row in data["rows"] if row["buckets"]["0_30"])
		result = receivables_api.add_collection_follow_up(
			overdue["invoice"], add_days(nowdate(), 2), "Promised by the CFO", add_days(nowdate(), 4), 200
		)
		task = frappe.get_doc("CRM Task", result["task"])
		self.assertEqual((task.tbo_task_type, float(task.tbo_promised_amount)), ("Collection", 200))
		self.assertIn("Promised by the CFO", task.description)
		row = next(
			row for row in receivables_api.get_receivables()["rows"] if row["invoice"] == overdue["invoice"]
		)
		self.assertEqual(row["promised_amount"], 200)

		frappe.set_user(ACCOUNTS)
		self.assertEqual(
			len({row["crm_deal"] for row in receivables_api.get_receivables()["rows"]} & {deal, other}), 2
		)
		with self.assertRaises(frappe.PermissionError):
			frappe.set_user(BDE)
			receivables_api.set_accounts_owner(deal, ACCOUNTS)
		frappe.set_user(ACCOUNTS)
		receivables_api.set_accounts_owner(deal, ACCOUNTS)
		self.assertEqual(frappe.db.get_value("CRM Deal", deal, "tbo_accounts_owner"), ACCOUNTS)

	def test_hidden_amounts_and_switch(self):
		enable_finance_receivables(finance_bde_can_see_amounts=0)
		frappe.set_user(BDE)
		with self.assertRaises(frappe.PermissionError):
			receivables_api.get_receivables()
		frappe.set_user("Administrator")
		frappe.db.set_single_value("TBO Smart Settings", "enable_finance_receivables", 0)
		with self.assertRaises(frappe.ValidationError):
			receivables_api.get_receivables()


class TestDigestAndSetup(ReceivablesTestCase):
	def test_digest(self):
		deal = make_finance_deal(BDE)
		make_deal_invoice(deal, rate=100, due_in=-1)
		old = make_deal_invoice(deal, rate=300, due_in=-35)
		digest = build_digest()
		self.assertIn(old.name, [row["invoice"] for row in digest["final_notice"]])
		self.assertTrue(any(row["days_overdue"] == 1 for row in digest["newly_overdue"]))
		enable_finance_receivables(management_digest_recipients="ceo@example.com, cfo@example.com")
		with patch("frappe.sendmail") as sendmail:
			send_digest()
		self.assertEqual(sendmail.call_args.kwargs["recipients"], ["ceo@example.com", "cfo@example.com"])

	def test_setup(self):
		for template in EMAIL_TEMPLATES:
			self.assertTrue(frappe.db.exists("Email Template", template))
		self.assertTrue(
			frappe.db.exists(
				"Property Setter", {"doc_type": "CRM Task", "property": "track_changes", "value": "1"}
			)
		)
		settings = frappe.get_single("TBO Smart Settings")
		settings.save(ignore_permissions=True)
		self.assertTrue(
			frappe.db.exists("CRM View Settings", {"dt": "CRM Task", "label": COLLECTIONS_VIEW, "public": 1})
		)


class TestReviewRules(ReceivablesTestCase):
	def test_a_failed_step_leaves_nothing_behind(self):
		deal = make_finance_deal(BDE)
		late = make_deal_invoice(deal, due_in=-16)
		with patch(
			"tbo_smart.finance.reminders.ReminderRun.make_dunning", side_effect=frappe.ValidationError
		):
			self.assertEqual(self.run_for(late.name), [])
		self.assertIsNone(get_collection_task(late.name))
		self.assertFalse(get_terms(late.name)[0].tbo_last_reminder)
		self.notify.assert_not_called()

	def test_request_is_frozen_once_sent(self):
		deal = make_finance_deal(BDE)
		invoice = make_deal_invoice(deal, due_in=-2)
		frappe.set_user(BDE)
		exception = exceptions_api.request_exception(
			invoice.name, "Pause request", "Holiday", pause_until=add_days(nowdate(), 3)
		)
		frappe.set_user("Administrator")
		doc = frappe.get_doc("TBO Finance Exception", exception["name"])
		doc.pause_until = add_days(nowdate(), 300)
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)

	def test_waiver_needs_a_real_credit_note(self):
		from erpnext.controllers.sales_and_purchase_return import make_return_doc

		deal = make_finance_deal(BDE)
		invoice = make_deal_invoice(deal, rate=300, due_in=-2)
		other = make_deal_invoice(deal, rate=300, due_in=-2)
		frappe.set_user(BDE)
		wrong = exceptions_api.request_exception(
			invoice.name,
			"Write-off request",
			"Goodwill",
			resolution_reference_doctype="Sales Invoice",
			resolution_reference_name=other.name,
		)
		frappe.set_user(APPROVER)
		with self.assertRaises(frappe.ValidationError):
			exceptions_api.decide_exception(wrong["name"], "approve")
		frappe.set_user("Administrator")
		note = make_return_doc("Sales Invoice", invoice.name)
		note.insert(ignore_permissions=True)
		note.submit()
		frappe.set_user(BDE)
		right = exceptions_api.request_exception(
			invoice.name,
			"Write-off request",
			"Goodwill",
			resolution_reference_doctype="Sales Invoice",
			resolution_reference_name=note.name,
		)
		frappe.set_user(APPROVER)
		self.assertEqual(exceptions_api.decide_exception(right["name"], "approve")["status"], "Approved")

	def test_digest_follows_approved_exceptions(self):
		deal = make_finance_deal(BDE)
		old = make_deal_invoice(deal, rate=300, due_in=-35)
		frappe.set_user(BDE)
		pause = exceptions_api.request_exception(
			old.name, "Pause request", "Holiday", pause_until=add_days(nowdate(), 3)
		)
		frappe.set_user(APPROVER)
		exceptions_api.decide_exception(pause["name"], "approve")
		frappe.set_user("Administrator")
		self.assertNotIn(old.name, [row["invoice"] for row in build_digest()["final_notice"]])
