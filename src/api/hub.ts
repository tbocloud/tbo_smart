/**
 * TBO Support hub API (teams.teambackoffice.com).
 *
 * Each function names the server method it stands in for. Bodies return mock
 * fixtures today; replace them with real requests without changing the
 * signatures the screens use.
 */
import { at } from "@/lib/clock";
import { daysFromToday, parse } from "@/lib/format";
import { db } from "@/mocks/db";
import { actionPlan, scoreboard } from "@/mocks/hub";
import { me, teammates } from "@/mocks/people";
import { fileText, projectFiles, projects } from "@/mocks/projects";

import { call, write } from "./client";
import type {
  ActionPlan,
  AppNotification,
  HoldReason,
  HomeCounts,
  Me,
  Person,
  Project,
  ProjectFile,
  Scoreboard,
  Task,
  TaskGroup,
  Ticket,
  TicketStatus,
  TicketView,
} from "./types";

const ME = me.full_name;

export function taskGroup(task: Task): TaskGroup {
  if (task.status === "On Hold") return "on_hold";
  if (task.status === "Pending Review") return "in_review";
  const diff = daysFromToday(task.exp_end_date);
  if (diff < 0) return "overdue";
  if (diff === 0) return "today";
  return "upcoming";
}

/**
 * One state per task, derived from status + timer, so the pill, the timer line
 * and the primary action can never disagree.
 */
export type WorkState = "not_started" | "running" | "paused" | "on_hold" | "in_review" | "done";

export function workState(task: Task): WorkState {
  if (task.status === "Completed") return "done";
  if (task.status === "Pending Review") return "in_review";
  if (task.status === "On Hold") return "on_hold";
  if (task.timer.state === "running") return "running";
  if (task.timer.state === "paused" || task.timer.logged_hours > 0) return "paused";
  return "not_started";
}

function openTasks(): Task[] {
  return db.tasks.filter((t) => t.status !== "Completed");
}

function mineTickets(): Ticket[] {
  return db.tickets.filter((t) => t.assigned_to === ME && t.status !== "Closed" && t.status !== "Resolved");
}

/** helpdesk.api.mobile.get_me */
export const getMe = () => call<Me>("hub", () => me);

/** helpdesk.api.mobile.get_home */
export const getHome = () =>
  call<HomeCounts>("hub", (empty) => {
    if (empty) {
      return {
        open_tickets: 0,
        awaiting_reply: 0,
        tasks_open: 0,
        tasks_due_today: 0,
        tasks_overdue: 0,
        tasks_on_hold: 0,
        escalated_to_head: 0,
        unread_notifications: 0,
      };
    }
    const open = openTasks();
    return {
      open_tickets: mineTickets().length,
      awaiting_reply: mineTickets().filter((t) => t.awaiting_reply).length,
      tasks_open: open.length,
      tasks_due_today: open.filter((t) => taskGroup(t) === "today").length,
      tasks_overdue: open.filter((t) => taskGroup(t) === "overdue").length,
      tasks_on_hold: open.filter((t) => taskGroup(t) === "on_hold").length,
      escalated_to_head: open.filter((t) => (t.escalation?.level ?? 0) >= 2).length,
      unread_notifications: db.notifications.filter((n) => n.site === "hub" && !n.read).length,
    };
  });

/** helpdesk.api.home.get_action_plan (AI, cached per day on the server) */
export const getActionPlan = () =>
  call<ActionPlan>("hub", (empty) => (empty ? { ...actionPlan, steps: [] } : actionPlan));

/** helpdesk.tasky.api.get_my_tasks */
export const getMyTasks = () => call<Task[]>("hub", (empty) => (empty ? [] : openTasks()));

/** helpdesk.tasky.api.get_task_detail */
export const getTask = (name: string) =>
  call<Task | null>("hub", () => db.tasks.find((t) => t.name === name) ?? null);

function mutateTask(name: string, change: (t: Task) => void, log: string, kind: Task["activity"][number]["kind"]) {
  return write("hub", () => {
    const task = db.tasks.find((t) => t.name === name);
    if (!task) return;
    change(task);
    task.activity.unshift({ id: `${Date.now()}`, at: at(0, "10:42"), who: ME, text: log, kind });
  });
}

/** helpdesk.tasky.api.update_task_status (status=Working) */
export const startTask = (name: string) =>
  mutateTask(
    name,
    (t) => {
      t.status = "Working";
      t.timer = { ...t.timer, state: "running", started_at: at(0, "10:42") };
    },
    "Started the timer",
    "time",
  );

/** helpdesk.tasky.api.update_task_status (pause the running timer) */
export const pauseTask = (name: string) =>
  mutateTask(name, (t) => (t.timer = { ...t.timer, state: "paused", started_at: undefined }), "Paused the timer", "time");

/** helpdesk.tasky.api.complete_task */
export const completeTask = (name: string, hoursSpent: number, note: string) =>
  mutateTask(
    name,
    (t) => {
      t.status = "Completed";
      t.timer = { state: "idle", logged_hours: t.timer.logged_hours + hoursSpent };
    },
    note ? `Completed · ${hoursSpent}h · ${note}` : `Completed · ${hoursSpent}h`,
    "status",
  );

/** helpdesk.tasky.api.hold_task */
export const holdTask = (name: string, reason: HoldReason, note: string) =>
  mutateTask(
    name,
    (t) => {
      t.status = "On Hold";
      t.hold_reason = reason;
      t.hold_note = note || undefined;
      t.timer = { ...t.timer, state: "idle", started_at: undefined };
    },
    `On hold: ${reason}`,
    "hold",
  );

/** helpdesk.tasky.api.resume_task */
export const resumeTask = (name: string) =>
  mutateTask(
    name,
    (t) => {
      t.status = "Open";
      t.hold_reason = undefined;
      t.hold_note = undefined;
    },
    "Resumed",
    "status",
  );

/** helpdesk.tasky.api.hand_over_task */
export const handOverTask = (name: string, to: Person, note: string) =>
  mutateTask(name, (t) => (t.status = "Open"), `Handed over to ${to.full_name}${note ? ` · ${note}` : ""}`, "assign");

/** helpdesk.tasky.api.request_help */
export const askForHelp = (name: string, to: Person, note: string) =>
  mutateTask(name, () => undefined, `Asked ${to.full_name} for help${note ? ` · ${note}` : ""}`, "comment");

/** People offered in the hand-over and help pickers. */
export const getTeammates = () => call<Person[]>("hub", () => teammates);

/** helpdesk.api.mobile.get_tickets */
export const getTickets = (view: TicketView) =>
  call<Ticket[]>("hub", (empty) => {
    if (empty) return [];
    const open = db.tickets.filter((t) => t.status !== "Closed" && t.status !== "Resolved");
    const list =
      view === "mine" ? open.filter((t) => t.assigned_to === ME) : view === "unassigned" ? open.filter((t) => !t.assigned_to) : open;
    return [...list].sort((a, b) => parse(a.sla_due).getTime() - parse(b.sla_due).getTime());
  });

/** helpdesk.api.mobile.get_ticket */
export const getTicket = (name: string) =>
  call<Ticket | null>("hub", () => db.tickets.find((t) => t.name === name) ?? null);

/** helpdesk.api.mobile.reply_ticket / comment_ticket */
export const replyTicket = (name: string, body: string, internal: boolean, attachments: string[]) =>
  write("hub", () => {
    const ticket = db.tickets.find((t) => t.name === name);
    if (!ticket) return;
    ticket.conversation.push({
      id: `${Date.now()}`,
      kind: internal ? "note" : "agent",
      author: ME,
      at: at(0, "10:42"),
      body,
      attachments: attachments.map((a) => ({ name: a, size: "120 KB" })),
    });
    if (!internal) {
      ticket.status = "Replied";
      ticket.awaiting_reply = false;
    }
  });

/** helpdesk.api.mobile.set_ticket_status */
export const setTicketStatus = (name: string, status: TicketStatus) =>
  write("hub", () => {
    const ticket = db.tickets.find((t) => t.name === name);
    if (ticket) ticket.status = status;
  });

/** helpdesk.api.project_files (projects I'm a member of) */
export const getProjects = () => call<Project[]>("hub", (empty) => (empty ? [] : projects));

export const getProject = (name: string) => call<Project | null>("hub", () => projects.find((p) => p.name === name) ?? null);

/** helpdesk.api.project_files.list_project_files */
export const getProjectFiles = (project: string) =>
  call<ProjectFile[]>("hub", (empty) => (empty ? [] : (projectFiles[project] ?? [])));

/** helpdesk.api.project_files.get_project_file_text */
export const getFile = (name: string) =>
  call<{ file: ProjectFile; text?: string } | null>("hub", () => {
    for (const files of Object.values(projectFiles)) {
      const file = files.find((f) => f.name === name);
      if (file) return { file, text: fileText[file.name] };
    }
    return null;
  });

/** helpdesk.api.mobile.get_notifications (the app merges these with ESS by time) */
export const getNotifications = () =>
  call<AppNotification[]>("hub", (empty) => (empty ? [] : db.notifications.filter((n) => n.site === "hub")));

/** helpdesk.api.mobile.mark_notifications_read */
export const markNotificationsRead = (names: string[] | "all") =>
  write("hub", () => {
    for (const n of db.notifications) {
      if (n.site === "hub" && (names === "all" || names.includes(n.name))) n.read = true;
    }
  });

/** helpdesk.api.work.get_scoreboard */
export const getScoreboard = () => call<Scoreboard>("hub", () => scoreboard);
