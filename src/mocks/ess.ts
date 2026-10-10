import type {
  AttendanceDay,
  AttendanceStatus,
  Employee,
  ExpenseClaim,
  Holiday,
  LeaveApplication,
  LeaveBalance,
  SalarySlip,
  Today,
} from "@/api/types";
import { day, isoDate, now } from "@/lib/clock";
import { webParam } from "@/lib/webParams";

export const employee: Employee = {
  name: "HR-EMP-00042",
  employee_name: "Najath Thekkeyil",
  user: "najath@teambackoffice.com",
  designation: "Frappe Developer",
  department: "ERP",
  company: "TBO India",
  branch: "Kakkanad, Kochi",
  reports_to: "Arun Babu",
  date_of_joining: "2024-06-03",
  phone: "+91 98470 •• ••21",
};

export function initialToday(): Today {
  const checkedIn = webParam("checkedin") === "1";
  return {
    shift_name: "General shift",
    shift_start: "09:30",
    shift_end: "18:30",
    status: checkedIn ? "checked_in" : "not_checked_in",
    checkins: checkedIn ? [{ log_type: "IN", time: `${day(0)}T09:41`, location: "TBO office, Kakkanad · ±12 m" }] : [],
  };
}

export const leaveBalances: LeaveBalance[] = [
  { leave_type: "Casual Leave", allocated: 12, used: 7, balance: 5 },
  { leave_type: "Sick Leave", allocated: 7, used: 2, balance: 5 },
  { leave_type: "Privilege Leave", allocated: 15, used: 5.5, balance: 9.5 },
  { leave_type: "Compensatory Off", allocated: 1, used: 0, balance: 1 },
];

export const leaveApprover = "Arun Babu";

export const leaveApplications: LeaveApplication[] = [
  {
    name: "LA-2026-0091",
    leave_type: "Casual Leave",
    from_date: "2026-10-23",
    to_date: "2026-10-23",
    half_day: true,
    days: 0.5,
    status: "Open",
    approver: leaveApprover,
    reason: "Bank visit for home loan papers",
  },
  {
    name: "LA-2026-0088",
    leave_type: "Casual Leave",
    from_date: "2026-10-07",
    to_date: "2026-10-07",
    half_day: false,
    days: 1,
    status: "Approved",
    approver: leaveApprover,
    reason: "Family function in Thrissur",
  },
  {
    name: "LA-2026-0084",
    leave_type: "Sick Leave",
    from_date: "2026-09-14",
    to_date: "2026-09-15",
    half_day: false,
    days: 2,
    status: "Approved",
    approver: leaveApprover,
    reason: "Fever",
  },
  {
    name: "LA-2026-0063",
    leave_type: "Privilege Leave",
    from_date: "2026-08-03",
    to_date: "2026-08-07",
    half_day: false,
    days: 5,
    status: "Rejected",
    approver: leaveApprover,
    reason: "Trip to Munnar. Rejected: Galom go-live week.",
  },
];

export const holidays: Holiday[] = [
  { date: "2026-01-01", description: "New Year's Day" },
  { date: "2026-01-26", description: "Republic Day" },
  { date: "2026-04-03", description: "Good Friday" },
  { date: "2026-04-14", description: "Vishu" },
  { date: "2026-05-01", description: "May Day" },
  { date: "2026-05-27", description: "Bakrid" },
  { date: "2026-08-15", description: "Independence Day" },
  { date: "2026-08-26", description: "Thiruvonam" },
  { date: "2026-10-02", description: "Gandhi Jayanti" },
  { date: "2026-10-20", description: "Vijayadashami" },
  { date: "2026-11-08", description: "Diwali" },
  { date: "2026-12-25", description: "Christmas" },
];

const leaveDays = new Set(["2026-09-14", "2026-09-15", "2026-10-07"]);
const absentDays = new Set(["2026-09-22"]);

/** Attendance for a month up to today, the way `get_attendance_calendar_events` reports it. */
export function attendanceFor(year: number, month: number): AttendanceDay[] {
  const today = isoDate(now());
  const holidaySet = new Set(holidays.map((h) => h.date));
  const out: AttendanceDay[] = [];
  const days = new Date(year, month + 1, 0).getDate();
  for (let d = 1; d <= days; d++) {
    const date = new Date(year, month, d);
    const iso = isoDate(date);
    if (iso > today) break;
    let status: AttendanceStatus = "Present";
    if (holidaySet.has(iso)) status = "Holiday";
    else if (date.getDay() === 0) status = "Weekly off";
    else if (leaveDays.has(iso)) status = "On Leave";
    else if (absentDays.has(iso)) status = "Absent";
    if (iso === today) continue; // today is shown from the punches, not a record yet
    out.push({ date: iso, status });
  }
  return out;
}

export const expenseTypes = ["Travel", "Food", "Internet", "Accommodation", "Other"];

export const expenseClaims: ExpenseClaim[] = [
  {
    name: "EXP-2026-0035",
    expense_type: "Food",
    amount: 480,
    date: "2026-10-08",
    description: "Lunch during the Galom go-live review",
    status: "Pending",
    approver: "Arun Babu",
    receipt: "bill-paragon-0810.jpg",
  },
  {
    name: "EXP-2026-0031",
    expense_type: "Travel",
    amount: 1240,
    date: "2026-10-06",
    description: "Client visit, Galom Kakkanad (cab both ways)",
    status: "Paid",
    approver: "Arun Babu",
    receipt: "uber-0610.pdf",
  },
  {
    name: "EXP-2026-0027",
    expense_type: "Internet",
    amount: 999,
    date: "2026-09-30",
    description: "Home broadband, September",
    status: "Approved",
    approver: "Arun Babu",
    receipt: "kerala-vision-sep.pdf",
  },
  {
    name: "EXP-2026-0022",
    expense_type: "Travel",
    amount: 2150,
    date: "2026-09-18",
    description: "Cab to Infopark. Rejected: the office cab covered this trip.",
    status: "Rejected",
    approver: "Arun Babu",
  },
];

export const salarySlips: SalarySlip[] = [
  { name: "Sal Slip/HR-EMP-00042/00009", month: "2026-09", gross: 52000, deductions: 5180, net_pay: 46820, working_days: 26, payment_days: 25 },
  { name: "Sal Slip/HR-EMP-00042/00008", month: "2026-08", gross: 52000, deductions: 5180, net_pay: 46820, working_days: 25, payment_days: 25 },
  { name: "Sal Slip/HR-EMP-00042/00007", month: "2026-07", gross: 52000, deductions: 5180, net_pay: 46820, working_days: 27, payment_days: 27 },
  { name: "Sal Slip/HR-EMP-00042/00006", month: "2026-06", gross: 48000, deductions: 4790, net_pay: 43210, working_days: 26, payment_days: 26 },
];
