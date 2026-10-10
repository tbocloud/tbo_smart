import { AlarmClock, ArrowUpRight, CalendarClock, CirclePause, Pause, Star, Timer } from "@/components/icons";
import { View } from "react-native";

import { taskGroup, workState } from "@/api/hub";
import type { Task } from "@/api/types";
import { Badge, Card, Text } from "@/components";
import { en } from "@/i18n/en";
import { dueInfo, hours, time } from "@/lib/format";
import { useTheme } from "@/theme/ThemeProvider";
import { space } from "@/theme/tokens";

export function TaskCard({ task, onPress }: { task: Task; onPress: () => void }) {
  const { colors } = useTheme();
  const due = dueInfo(task.exp_end_date);
  const group = taskGroup(task);
  const showDue = group !== "in_review" && group !== "on_hold";
  return (
    <Card
      onPress={onPress}
      accent={group === "overdue" ? colors.danger : undefined}
      accessibilityLabel={[
        task.subject,
        task.project_name,
        task.is_key ? en.work.keyTask : null,
        showDue ? due.text : task.status,
        task.escalation?.label,
      ]
        .filter(Boolean)
        .join(", ")}
    >
      <View style={{ gap: space.sm }}>
        <View style={{ flexDirection: "row", alignItems: "center", gap: space.sm }}>
          <Text variant="small" color="ink5" numberOfLines={1} style={{ flex: 1 }}>
            {task.project_name}
            {task.customer ? ` · ${task.customer}` : ""}
          </Text>
          {task.is_key ? (
            <View style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
              <Star size={14} color={colors.warning} fill={colors.warning} />
              <Text variant="caption" weight="medium" color="warning">
                {en.work.keyTask}
              </Text>
            </View>
          ) : null}
        </View>
        <Text variant="title" weight="semibold" numberOfLines={2}>
          {task.subject}
        </Text>
        <Text variant="small" color="ink6">
          {en.work.assignedBy(task.assigned_by.full_name)}
        </Text>
        <View style={{ flexDirection: "row", flexWrap: "wrap", gap: space.sm }}>
          {showDue ? (
            <Badge
              label={due.text}
              tone={due.tone === "neutral" ? "neutral" : due.tone}
              icon={due.overdue ? AlarmClock : CalendarClock}
            />
          ) : null}
          {task.escalation ? <Badge label={task.escalation.label} tone="danger" icon={ArrowUpRight} /> : null}
        </View>
        {task.hold_reason ? (
          <View
            style={{
              flexDirection: "row",
              gap: space.sm,
              padding: space.md,
              borderRadius: 8,
              backgroundColor: colors.warningSoft,
            }}
          >
            <CirclePause size={16} color={colors.warning} style={{ marginTop: 2 }} />
            <View style={{ flex: 1, gap: 2 }}>
              <Text variant="small" weight="medium" color="warning">
                {task.hold_reason}
              </Text>
              {task.hold_note ? (
                <Text variant="small" color="ink7">
                  {task.hold_note}
                </Text>
              ) : null}
            </View>
          </View>
        ) : null}
        <TimerLine task={task} />
      </View>
    </Card>
  );
}

export function TimerLine({ task }: { task: Task }) {
  const { colors } = useTheme();
  const { started_at, logged_hours } = task.timer;
  const state = workState(task);
  if (state === "not_started") return null;
  const running = state === "running";
  const Icon = running ? Timer : Pause;
  const label =
    running && started_at
      ? en.work.timerRunning(time(started_at))
      : state === "paused"
        ? en.work.timerPaused(hours(logged_hours))
        : en.work.logged(hours(logged_hours));
  return (
    <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
      <Icon size={14} color={running ? colors.info : colors.ink5} />
      <Text variant="small" mono color={running ? "info" : "ink5"}>
        {label}
      </Text>
    </View>
  );
}
