import type { ActionPlan, AppNotification, Scoreboard } from "@/api/types";
import { at } from "@/lib/clock";

export const actionPlan: ActionPlan = {
  generated_at: at(0, "09:05"),
  steps: [
    {
      id: "s1",
      text: "Reply to Haris on #1279",
      detail: "Past its SLA, and he needs 12 invoices out before noon.",
      route: "/ticket/1279",
    },
    {
      id: "s2",
      text: "Finish “TBO ERP: migrate customers”",
      detail: "Overdue since Thursday and escalated to Arun. Paused at 3.5h of 6h; resume it first.",
      route: "/task/TASK-00412",
    },
    {
      id: "s3",
      text: "Ship the Avera leave approval fix",
      detail: "Due 6:00 PM. Your timer is already running.",
      route: "/task/TASK-00431",
    },
    {
      id: "s4",
      text: "Ask Galom for the e-invoice credentials",
      detail: "The GST task stays on hold until Rahul shares them.",
      route: "/task/TASK-00405",
    },
  ],
};

export const notifications: AppNotification[] = [
  {
    name: "N-9001",
    site: "hub",
    title: "Haris K replied on #1279",
    body: "Any update? We need to send 12 invoices out before noon.",
    at: at(0, "08:55"),
    read: false,
    route: "/ticket/1279",
  },
  {
    name: "N-9002",
    site: "hub",
    title: "Escalated: TBO ERP: migrate customers",
    body: "2 days overdue. Arun Babu has been told.",
    at: at(0, "09:15"),
    read: false,
    route: "/task/TASK-00412",
  },
  {
    name: "E-3101",
    site: "ess",
    title: "Leave approved",
    body: "Arun Babu approved your Sick Leave for 14–15 Sep.",
    at: at(0, "08:10"),
    read: false,
    route: "/hr/leave",
  },
  {
    name: "N-8994",
    site: "hub",
    title: "New ticket assigned: #1284",
    body: "Galom International Private Limited · Unable to submit expense claim",
    at: at(0, "08:21"),
    read: true,
    route: "/ticket/1284",
  },
  {
    name: "N-8990",
    site: "hub",
    title: "File for you in TBO ERP",
    body: "Niranjana C shared “Data mapping notes.md”.",
    at: at(-1, "15:00"),
    read: true,
    route: "/project/PROJ-0003",
  },
  {
    name: "E-3098",
    site: "ess",
    title: "Expense claim paid",
    body: "EXP-2026-0031 · ₹1,240 for the Galom client visit.",
    at: at(-1, "12:30"),
    read: true,
    route: "/hr/expenses",
  },
  {
    name: "E-3090",
    site: "ess",
    title: "Salary slip for September",
    body: "Your September 2026 slip is ready.",
    at: at(-9, "10:00"),
    read: true,
    route: "/hr/salary",
  },
];

export const scoreboard: Scoreboard = {
  period_label: "This week",
  range: "5 – 11 Oct 2026",
  champion: { full_name: "Mufliha", score: 21.6, tasks_done: 19, key_tasks: 1, on_time_pct: 100, hours: 31.5 },
  me: {
    full_name: "Najath Thekkeyil",
    score: 12.4,
    tasks_done: 6,
    key_tasks: 1,
    on_time_pct: 83,
    hours: 27,
    rank: 4,
    of: 35,
    delta_rank: 2,
    breakdown: [
      { label: "5 tasks done on time", points: 9 },
      { label: "1 key task", points: 1 },
      { label: "1 task done late", points: 0.5 },
      { label: "27h logged", points: 1.9 },
    ],
  },
  leaders: [
    { rank: 1, full_name: "Mufliha", score: 21.6, tasks_done: 19, key_tasks: 1, on_time_pct: 100, hours: 31.5 },
    { rank: 2, full_name: "Niranjana C", score: 14.9, tasks_done: 9, key_tasks: 2, on_time_pct: 89, hours: 38 },
    { rank: 3, full_name: "Ajmal M", score: 13.3, tasks_done: 11, key_tasks: 0, on_time_pct: 91, hours: 29 },
    { rank: 4, full_name: "Najath Thekkeyil", score: 12.4, tasks_done: 6, key_tasks: 1, on_time_pct: 83, hours: 27 },
    { rank: 5, full_name: "Reshma R", score: 9.8, tasks_done: 7, key_tasks: 0, on_time_pct: 50, hours: 33 },
  ],
};
