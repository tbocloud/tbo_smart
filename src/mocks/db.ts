/**
 * In-memory copy of the fixtures that mock "writes" change, so actions like
 * Start, Hold or Check in visibly update every screen during a demo. Resets
 * on reload.
 */
import { initialToday, expenseClaims, leaveApplications } from "./ess";
import { notifications } from "./hub";
import { tasks } from "./tasks";
import { tickets } from "./tickets";

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T;

export const db = {
  tasks: clone(tasks),
  tickets: clone(tickets),
  notifications: clone(notifications),
  today: initialToday(),
  leaveApplications: clone(leaveApplications),
  expenseClaims: clone(expenseClaims),
};
