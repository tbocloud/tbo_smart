/**
 * TBO India ESS API (tboindia.tbocloud.in): the `tbo_smart` custom app plus
 * the HRMS PWA methods in `hrms.api`. Mock bodies today; same signatures later.
 */
import { at } from "@/lib/clock";
import { dayCount } from "@/lib/format";
import { db } from "@/mocks/db";
import {
  attendanceFor,
  employee,
  expenseTypes,
  holidays,
  leaveApprover,
  leaveBalances,
  salarySlips,
} from "@/mocks/ess";

import { call, write } from "./client";
import type {
  AppNotification,
  AttendanceDay,
  Employee,
  ExpenseClaim,
  Holiday,
  LeaveApplication,
  LeaveBalance,
  SalarySlip,
  Today,
} from "./types";

/** tbo_smart.api.get_me */
export const getEmployee = () => call<Employee>("ess", () => employee);

/** tbo_smart.api.get_today */
export const getToday = () => call<Today>("ess", () => db.today);

export type Fix = { latitude: number; longitude: number; accuracy: number };

/**
 * tbo_smart.api.check_in. The server stamps the time; the app only sends the
 * location fix. Returns the recorded punch.
 */
export const checkIn = (logType: "IN" | "OUT", fix: Fix) =>
  write("ess", () => {
    const punch = {
      log_type: logType,
      time: at(0, "10:42"),
      location: `TBO office, Kakkanad · ±${Math.round(fix.accuracy)} m`,
    };
    db.today.checkins.push(punch);
    db.today.status = logType === "IN" ? "checked_in" : "checked_out";
    return punch;
  });

/** hrms.api.get_attendance_calendar_events */
export const getAttendance = (year: number, month: number) =>
  call<AttendanceDay[]>("ess", (empty) => (empty ? [] : attendanceFor(year, month)));

/** hrms.api.get_leave_balance_map */
export const getLeaveBalances = () => call<LeaveBalance[]>("ess", (empty) => (empty ? [] : leaveBalances));

/** hrms.api.get_leave_applications */
export const getLeaveApplications = () =>
  call<LeaveApplication[]>("ess", (empty) => (empty ? [] : db.leaveApplications));

/** hrms.api.get_leave_approval_details */
export const getLeaveApprover = () => call<string>("ess", () => leaveApprover);

export type LeaveDraft = {
  leave_type: string;
  from_date: string;
  to_date: string;
  half_day: boolean;
  reason: string;
};

/** frappe.client.insert → Leave Application */
export const applyLeave = (draft: LeaveDraft) =>
  write("ess", () => {
    const app: LeaveApplication = {
      ...draft,
      name: `LA-2026-00${92 + db.leaveApplications.length}`,
      days: dayCount(draft.from_date, draft.to_date, draft.half_day),
      status: "Open",
      approver: leaveApprover,
    };
    db.leaveApplications.unshift(app);
    return app;
  });

/** frappe.client.set_value → Leave Application status Cancelled (pending only) */
export const cancelLeave = (name: string) =>
  write("ess", () => {
    const app = db.leaveApplications.find((a) => a.name === name);
    if (app && app.status === "Open") app.status = "Cancelled";
  });

/** hrms.api.get_holidays_for_employee */
export const getHolidays = () => call<Holiday[]>("ess", (empty) => (empty ? [] : holidays));

/** hrms.api.get_expense_claims */
export const getExpenseClaims = () => call<ExpenseClaim[]>("ess", (empty) => (empty ? [] : db.expenseClaims));

/** hrms.api.get_expense_claim_types */
export const getExpenseTypes = () => call<string[]>("ess", () => expenseTypes);

export type ClaimDraft = {
  expense_type: string;
  amount: number;
  date: string;
  description: string;
  receipt?: string;
};

/** frappe.client.insert → Expense Claim, then hrms.api.upload_base64_file for the receipt */
export const createClaim = (draft: ClaimDraft) =>
  write("ess", () => {
    const claim: ExpenseClaim = {
      ...draft,
      name: `EXP-2026-00${36 + db.expenseClaims.length}`,
      status: "Pending",
      approver: leaveApprover,
    };
    db.expenseClaims.unshift(claim);
    return claim;
  });

/** hrms.api.get_salary_slips (list) + download_salary_slip (PDF) */
export const getSalarySlips = () => call<SalarySlip[]>("ess", (empty) => (empty ? [] : salarySlips));

/** frappe Notification Log on tboindia */
export const getNotifications = () =>
  call<AppNotification[]>("ess", (empty) => (empty ? [] : db.notifications.filter((n) => n.site === "ess")));

/** hrms.api.mark_all_notifications_as_read */
export const markNotificationsRead = (names: string[] | "all") =>
  write("ess", () => {
    for (const n of db.notifications) {
      if (n.site === "ess" && (names === "all" || names.includes(n.name))) n.read = true;
    }
  });

