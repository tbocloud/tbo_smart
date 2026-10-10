import {
  AlarmClock,
  Ban,
  CircleCheck,
  CircleDot,
  CirclePause,
  Clock,
  Eye,
  Reply,
  type LucideIcon,
} from "@/components/icons";

import type { Tone } from "@/theme/tokens";

import { Badge } from "./Badge";

/** One place that maps every backend status to its tone and icon. */
const MAP: Record<string, { tone: Tone; icon: LucideIcon }> = {
  // tickets
  Open: { tone: "info", icon: CircleDot },
  Replied: { tone: "neutral", icon: Reply },
  Paused: { tone: "neutral", icon: CirclePause },
  Resolved: { tone: "success", icon: CircleCheck },
  Closed: { tone: "neutral", icon: CircleCheck },
  // tasks
  Working: { tone: "info", icon: Clock },
  // derived task work states (api/hub.ts workState)
  not_started: { tone: "neutral", icon: CircleDot },
  running: { tone: "info", icon: Clock },
  paused: { tone: "neutral", icon: CirclePause },
  on_hold: { tone: "warning", icon: CirclePause },
  in_review: { tone: "neutral", icon: Eye },
  done: { tone: "success", icon: CircleCheck },
  "Pending Review": { tone: "neutral", icon: Eye },
  "On Hold": { tone: "warning", icon: CirclePause },
  Completed: { tone: "success", icon: CircleCheck },
  // ESS
  Pending: { tone: "warning", icon: Clock },
  Approved: { tone: "success", icon: CircleCheck },
  Paid: { tone: "success", icon: CircleCheck },
  Rejected: { tone: "danger", icon: Ban },
  Cancelled: { tone: "neutral", icon: Ban },
  Overdue: { tone: "danger", icon: AlarmClock },
};

export function StatusPill({ status, label }: { status: string; label?: string }) {
  const m = MAP[status] ?? { tone: "neutral" as Tone, icon: CircleDot };
  return <Badge label={label ?? status} tone={m.tone} icon={m.icon} />;
}
