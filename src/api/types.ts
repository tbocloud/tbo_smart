/**
 * Response shapes the app builds against. They mirror the backend brief
 * (TBO-Smart-App-Frappe-Developer-Prompt.md §3): hub methods in
 * `helpdesk.api.mobile` / `helpdesk.tasky.api`, ESS methods in `tbo_smart.api`
 * and `hrms.api`. When the real contract (`docs/tbo-smart-api.md`) lands,
 * adjust these types first and let the compiler point at every screen to fix.
 */

export type Site = "hub" | "ess";

// ---------- Hub (TBO Support) ----------

export type Person = {
  user: string; // email, the identity on both sites
  full_name: string;
  image?: string;
  role?: string;
};

export type Me = Person & {
  roles: string[];
  is_manager: boolean;
  department: string;
  teams: string[];
};

export type HomeCounts = {
  open_tickets: number;
  awaiting_reply: number;
  tasks_open: number;
  tasks_due_today: number;
  tasks_overdue: number;
  tasks_on_hold: number;
  escalated_to_head: number;
  unread_notifications: number;
};

export type PlanStep = {
  id: string;
  text: string;
  detail: string;
  route?: string;
};

export type ActionPlan = {
  steps: PlanStep[];
  generated_at: string;
};

export type TaskStatus = "Open" | "Working" | "Pending Review" | "On Hold" | "Completed";
export type TaskGroup = "overdue" | "today" | "upcoming" | "on_hold" | "in_review";
export type HoldReason =
  | "Waiting on customer"
  | "Waiting on a teammate"
  | "Blocked by another task"
  | "Waiting for access"
  | "Other";

export type TaskActivity = {
  id: string;
  at: string;
  who: string;
  text: string;
  kind: "status" | "time" | "comment" | "hold" | "assign" | "escalation";
};

export type Task = {
  name: string;
  assigned_to: string; // user email
  subject: string;
  project: string;
  project_name: string;
  customer?: string;
  status: TaskStatus;
  exp_end_date: string; // ISO datetime
  is_key: boolean;
  assigned_by: Person;
  hold_reason?: HoldReason;
  hold_note?: string;
  escalation?: { level: number; label: string };
  timer: { state: "running" | "paused" | "idle"; started_at?: string; logged_hours: number };
  expected_hours?: number;
  description: string;
  activity: TaskActivity[];
};

export type TicketPriority = "Low" | "Medium" | "High" | "Urgent";
export type TicketStatus = "Open" | "Replied" | "Paused" | "Resolved" | "Closed";
export type TicketView = "mine" | "unassigned" | "team";

export type Attachment = { name: string; size: string };

export type TicketMessage = {
  id: string;
  kind: "customer" | "agent" | "note";
  author: string;
  at: string;
  body: string;
  attachments?: Attachment[];
};

export type Ticket = {
  name: string;
  subject: string;
  customer: string;
  contact: string;
  priority: TicketPriority;
  status: TicketStatus;
  sla_due: string; // ISO datetime, resolution or first-response due
  assigned_to?: string;
  team: string;
  opened_at: string;
  awaiting_reply: boolean;
  conversation: TicketMessage[];
};

export type Project = {
  name: string;
  project_name: string;
  customer: string;
  department: string;
  my_role: string;
  open_tasks: number;
  files: number;
  for_you: number;
};

export type ProjectFile = {
  name: string;
  file_name: string;
  folder: string; // "" is the project root; "Prompts/Phase 1" is nested
  kind: "md" | "pdf";
  size: string;
  modified: string;
  uploaded_by: string;
  for_you: boolean;
  status?: "Final" | "Draft" | "Needs review";
};

export type AppNotification = {
  name: string;
  site: Site;
  title: string;
  body: string;
  at: string;
  read: boolean;
  route: string;
};

export type ScoreLine = {
  full_name: string;
  score: number;
  tasks_done: number;
  key_tasks: number;
  on_time_pct: number;
  hours: number;
};

export type Scoreboard = {
  period_label: string;
  range: string;
  champion: ScoreLine;
  me: ScoreLine & { rank: number; of: number; delta_rank: number; breakdown: { label: string; points: number }[] };
  leaders: (ScoreLine & { rank: number })[];
  departments: DepartmentScore[];
};

// ---------- ESS (TBO India HR) ----------

export type Employee = {
  name: string;
  employee_name: string;
  user: string;
  designation: string;
  department: string;
  company: string;
  branch: string;
  reports_to: string;
  date_of_joining: string;
  phone: string;
};

export type Checkin = { log_type: "IN" | "OUT"; time: string; location: string };

export type Today = {
  shift_name: string;
  shift_start: string; // "09:30"
  shift_end: string; // "18:30"
  status: "not_checked_in" | "checked_in" | "checked_out";
  checkins: Checkin[];
};

export type LeaveBalance = {
  leave_type: string;
  allocated: number;
  used: number;
  balance: number;
};

export type LeaveStatus = "Open" | "Approved" | "Rejected" | "Cancelled";

export type LeaveApplication = {
  name: string;
  leave_type: string;
  from_date: string;
  to_date: string;
  half_day: boolean;
  days: number;
  status: LeaveStatus;
  approver: string;
  reason: string;
};

export type Holiday = { date: string; description: string };

export type AttendanceStatus = "Present" | "Absent" | "On Leave" | "Holiday" | "Weekly off";
export type AttendanceDay = { date: string; status: AttendanceStatus };

export type ExpenseStatus = "Pending" | "Approved" | "Rejected" | "Paid";

export type ExpenseClaim = {
  name: string;
  expense_type: string;
  amount: number;
  date: string;
  description: string;
  status: ExpenseStatus;
  approver: string;
  receipt?: string;
};

export type SalarySlip = {
  name: string;
  month: string; // "2026-09"
  gross: number;
  deductions: number;
  net_pay: number;
  working_days: number;
  payment_days: number;
};

// ---------- CRM (Frappe CRM + ERPNext on tboindia) ----------

export type LeadStatus = "New" | "Contacted" | "Qualified" | "Unqualified";

export type CrmActivity = {
  id: string;
  at: string;
  who: string;
  kind: "call" | "email" | "meeting" | "note" | "whatsapp" | "stage";
  text: string;
};

export type CrmNote = { id: string; at: string; who: string; text: string };

export type Lead = {
  name: string;
  lead_name: string;
  organization: string;
  designation?: string;
  status: LeadStatus;
  source: string;
  owner: string;
  phone: string;
  whatsapp?: string;
  email: string;
  territory: string;
  last_contact?: string;
  next_follow_up?: string;
  notes: CrmNote[];
  activities: CrmActivity[];
};

export type DealStage = "Qualification" | "Demo" | "Proposal" | "Negotiation" | "Won" | "Lost";

export type Deal = {
  name: string;
  title: string;
  organization: string; // CRM Organization / ERPNext Customer name
  contact: string;
  stage: DealStage;
  amount: number; // INR
  probability: number;
  expected_close: string;
  owner: string;
  closed_on?: string;
  lost_reason?: string;
  stage_history: { stage: DealStage; at: string }[];
  activities: CrmActivity[];
  tasks: { id: string; title: string; due: string; done: boolean }[];
  notes: CrmNote[];
};

export type CustomerHealth = "Good" | "At risk";

export type OrgContact = { name: string; designation: string; phone: string; email: string };

export type Organization = {
  name: string; // ERPNext Customer
  territory: string;
  industry: string;
  health: CustomerHealth;
  health_reason: string;
  amc?: { hours_total: number; hours_used: number; renews_on: string };
  contacts: OrgContact[];
};

export type FollowUp = {
  id: string;
  kind: "call" | "meeting";
  subject: string;
  with: string;
  organization: string;
  due: string;
  owner: string;
  ref: { doctype: "lead" | "deal"; name: string };
};

// ---------- Management ----------

export type TeamMember = Person & {
  id: string;
  department: string;
  attendance: "in" | "not_yet" | "leave";
  checked_in_at?: string;
  leave_type?: string;
  overdue: number;
  escalated: number;
  open_tasks: number;
  score: number;
  on_time_pct: number;
  leave_balance: number;
  tasks: { name: string; subject: string; project: string; due: string; status: string }[];
};

export type Escalation = {
  id: string;
  kind: "task" | "ticket";
  ref: string; // TASK-… or ticket number
  title: string;
  owner: string;
  level: number;
  since: string; // when it first went overdue / breached
  reason: string;
};

export type ApprovalKind = "leave" | "expense" | "review";

export type Approval = {
  id: string;
  kind: ApprovalKind;
  who: string;
  title: string;
  detail: string;
  submitted: string;
  amount?: number;
  ref: string;
};

export type Rag = "green" | "amber" | "red";

export type ProjectHealth = {
  name: string;
  project_name: string;
  customer: string;
  rag: Rag;
  progress: number; // 0–100
  overdue: number;
  next_milestone: { title: string; due: string };
  hours_used: number;
  hours_estimate: number;
};

export type SlaSummary = {
  open: number;
  breaching_soon: number;
  breached: number;
  unassigned: number;
  oldest_unassigned?: Ticket;
};

export type DepartmentScore = {
  department: string;
  people: number;
  done: number;
  on_time_pct: number | null;
  overdue: number;
  hours: number;
  champion?: string;
};
