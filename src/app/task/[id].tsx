import { useLocalSearchParams } from "expo-router";
import {
  AlarmClock,
  ArrowUpRight,
  CalendarClock,
  CircleCheck,
  CirclePause,
  Hand,
  LifeBuoy,
  MessageSquare,
  Pause,
  Play,
  Star,
  Timer,
  UserRound,
  UserRoundPlus,
  type LucideIcon,
} from "@/components/icons";
import { useState } from "react";
import { View } from "react-native";

import {
  askForHelp,
  completeTask,
  getTask,
  getTeammates,
  workState,
  handOverTask,
  holdTask,
  pauseTask,
  resumeTask,
  startTask,
} from "@/api/hub";
import type { Person, Task, TaskActivity } from "@/api/types";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Group,
  ListRow,
  QueryView,
  Screen,
  SectionHeader,
  StatusPill,
  Text,
  ToneIcon,
  useToast,
} from "@/components";
import { TimerLine } from "@/features/tasks/TaskCard";
import { CompleteSheet, HoldSheet, PersonSheet } from "@/features/tasks/TaskSheets";
import { en } from "@/i18n/en";
import { writeErrorMessage } from "@/lib/errors";
import { dayLabel, dueInfo, hours, time } from "@/lib/format";
import { useMutation, useQuery } from "@/lib/query";
import { useTheme } from "@/theme/ThemeProvider";
import { space, type Tone } from "@/theme/tokens";

type SheetName = "complete" | "hold" | "handover" | "help" | null;

export default function TaskDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const task = useQuery(`hub:task:${id}`, () => getTask(id));
  const t = task.data;

  return (
    <Screen
      back
      title={en.task.title}
      subtitle={id}
      onRefresh={task.refresh}
      refreshing={task.refreshing}
      footer={t ? <TaskFooter task={t} /> : undefined}
    >
      <QueryView query={task}>
        {(data) => (data ? <TaskBody task={data} /> : <EmptyState icon={CirclePause} title={en.states.notFound} />)}
      </QueryView>
    </Screen>
  );
}

function TaskBody({ task }: { task: Task }) {
  const { colors } = useTheme();
  const state = workState(task);
  const due = dueInfo(task.exp_end_date);
  const showDue = task.status !== "Pending Review" && task.status !== "Completed";
  return (
    <>
      <View style={{ gap: space.sm }}>
        <Text variant="small" color="ink5">
          {task.project_name}
          {task.customer ? ` · ${task.customer}` : ""}
        </Text>
        <Text variant="h2" weight="semibold" accessibilityRole="header">
          {task.subject}
        </Text>
        <View style={{ flexDirection: "row", flexWrap: "wrap", gap: space.sm, marginTop: space.xs }}>
          <StatusPill status={state} label={en.task.states[state]} />
          {showDue ? (
            <Badge label={due.text} tone={due.tone} icon={due.overdue ? AlarmClock : CalendarClock} />
          ) : null}
          {task.is_key ? <Badge label={en.work.keyTask} tone="warning" icon={Star} /> : null}
          {task.escalation ? <Badge label={task.escalation.label} tone="danger" icon={ArrowUpRight} /> : null}
        </View>
      </View>

      <Card>
        <View style={{ gap: space.md }}>
          <Meta icon={UserRound} label={en.work.assignedBy(task.assigned_by.full_name)} />
          <Meta icon={CalendarClock} label={`${en.task.due} ${dayLabel(task.exp_end_date)}, ${time(task.exp_end_date)}`} />
          {task.expected_hours ? (
            <Meta icon={Timer} label={en.task.expected(hours(task.expected_hours))} />
          ) : null}
          <TimerLine task={task} />
        </View>
      </Card>

      {task.hold_reason ? (
        <View style={{ flexDirection: "row", gap: space.md, padding: space.lg, borderRadius: 12, backgroundColor: colors.warningSoft }}>
          <CirclePause size={18} color={colors.warning} style={{ marginTop: 2 }} />
          <View style={{ flex: 1, gap: 2 }}>
            <Text variant="body" weight="semibold" color="warning">
              {en.task.holdReason}: {task.hold_reason}
            </Text>
            {task.hold_note ? (
              <Text variant="body" color="ink7">
                {task.hold_note}
              </Text>
            ) : null}
          </View>
        </View>
      ) : null}

      <View style={{ gap: space.sm }}>
        <SectionHeader title={en.task.description} />
        <Card>
          <Text variant="body" color="ink7" selectable>
            {task.description}
          </Text>
        </Card>
      </View>

      <OtherActions task={task} />

      <View style={{ gap: space.sm }}>
        <SectionHeader title={en.task.activity} count={task.activity.length} />
        <Card>
          <View style={{ gap: space.lg }}>
            {task.activity.map((a) => (
              <ActivityRow key={a.id} item={a} />
            ))}
          </View>
        </Card>
      </View>
    </>
  );
}

function Meta({ icon: Icon, label }: { icon: LucideIcon; label: string }) {
  const { colors } = useTheme();
  return (
    <View style={{ flexDirection: "row", alignItems: "center", gap: space.sm }}>
      <Icon size={16} color={colors.ink5} />
      <Text variant="body" color="ink7" style={{ flex: 1 }}>
        {label}
      </Text>
    </View>
  );
}

const ACTIVITY: Record<TaskActivity["kind"], { icon: LucideIcon; tone: Tone }> = {
  status: { icon: CircleCheck, tone: "neutral" },
  time: { icon: Timer, tone: "info" },
  comment: { icon: MessageSquare, tone: "neutral" },
  hold: { icon: CirclePause, tone: "neutral" },
  assign: { icon: UserRoundPlus, tone: "neutral" },
  escalation: { icon: ArrowUpRight, tone: "danger" },
};

function ActivityRow({ item }: { item: TaskActivity }) {
  const a = ACTIVITY[item.kind];
  return (
    <View style={{ flexDirection: "row", gap: space.md }}>
      <ToneIcon icon={a.icon} tone={a.tone} size={28} />
      <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
        <Text variant="body" color="ink7">
          {item.text}
        </Text>
        <Text variant="caption" color="ink5">
          {item.who} ·{" "}
          <Text variant="caption" mono color="ink5">
            {dayLabel(item.at)}, {time(item.at)}
          </Text>
        </Text>
      </View>
    </View>
  );
}

/** Secondary actions sit in the content, away from the primary action in the footer. */
function OtherActions({ task }: { task: Task }) {
  const toast = useToast();
  const [sheet, setSheetState] = useState<SheetName>(null);
  const [opened, setOpened] = useState(0);
  const setSheet = (name: SheetName) => {
    if (name) setOpened((n) => n + 1);
    setSheetState(name);
  };
  const people = useQuery("hub:teammates", getTeammates);
  const keys = [`hub:task:${task.name}`, "hub:tasks", "hub:home"];
  const onError = (e: Parameters<typeof writeErrorMessage>[0]) => toast(writeErrorMessage(e), "danger");

  const hold = useMutation(holdTask, {
    invalidates: keys,
    onSuccess: () => {
      setSheet(null);
      toast(en.task.held, "success");
    },
    onError,
  });
  const hand = useMutation((to: Person, note: string) => handOverTask(task.name, to, note).then(() => to), {
    invalidates: keys,
    onSuccess: (to) => {
      setSheet(null);
      toast(en.task.handedOver(to.full_name), "success");
    },
    onError,
  });
  const help = useMutation((to: Person, note: string) => askForHelp(task.name, to, note).then(() => to), {
    invalidates: keys,
    onSuccess: (to) => {
      setSheet(null);
      toast(en.task.helpAsked(to.full_name), "success");
    },
    onError,
  });

  if (task.status === "Completed" || task.status === "Pending Review") return null;
  const canHold = task.status !== "On Hold";

  return (
    <>
      <Group>
        {canHold ? (
          <ListRow
            title={en.task.hold}
            subtitle={en.task.holdWhy}
            leading={<ToneIcon icon={CirclePause} size={32} />}
            onPress={() => setSheet("hold")}
          />
        ) : null}
        <ListRow
          title={en.task.handOver}
          leading={<ToneIcon icon={Hand} size={32} />}
          onPress={() => setSheet("handover")}
        />
        <ListRow
          title={en.task.askHelp}
          leading={<ToneIcon icon={LifeBuoy} size={32} />}
          onPress={() => setSheet("help")}
          divider={false}
        />
      </Group>
      <HoldSheet
        key={`hold-${opened}`}
        open={sheet === "hold"}
        onClose={() => setSheet(null)}
        pending={hold.pending}
        subject={task.subject}
        onSubmit={(reason, note) => void hold.mutate(task.name, reason, note)}
      />
      <PersonSheet
        key={`handover-${opened}`}
        mode="handover"
        open={sheet === "handover"}
        onClose={() => setSheet(null)}
        pending={hand.pending}
        people={people.data ?? []}
        onSubmit={(to, note) => void hand.mutate(to, note)}
      />
      <PersonSheet
        key={`help-${opened}`}
        mode="help"
        open={sheet === "help"}
        onClose={() => setSheet(null)}
        pending={help.pending}
        people={people.data ?? []}
        onSubmit={(to, note) => void help.mutate(to, note)}
      />
    </>
  );
}

function TaskFooter({ task }: { task: Task }) {
  const toast = useToast();
  const [completing, setCompleting] = useState(false);
  const [opened, setOpened] = useState(0);
  const keys = [`hub:task:${task.name}`, "hub:tasks", "hub:home"];
  const onError = (e: Parameters<typeof writeErrorMessage>[0]) => toast(writeErrorMessage(e), "danger");
  const state = workState(task);
  const start = useMutation(startTask, {
    invalidates: keys,
    onSuccess: () => toast(state === "paused" ? en.task.timerResumed : en.task.started),
    onError,
  });
  const pause = useMutation(pauseTask, { invalidates: keys, onSuccess: () => toast(en.task.paused), onError });
  const resume = useMutation(resumeTask, { invalidates: keys, onSuccess: () => toast(en.task.resumed, "success"), onError });
  const complete = useMutation(completeTask, {
    invalidates: keys,
    onSuccess: () => {
      setCompleting(false);
      toast(en.task.completed, "success");
    },
    onError,
  });

  if (task.status === "Completed" || task.status === "Pending Review") {
    return (
      <Text variant="body" color="ink6" style={{ textAlign: "center", paddingVertical: space.md }}>
        {task.status === "Completed" ? en.task.readOnlyDone : en.task.inReview}
      </Text>
    );
  }
  if (task.status === "On Hold") {
    return (
      <Button
        label={en.task.resume}
        icon={Play}
        variant="primary"
        size="lg"
        full
        loading={resume.pending}
        onPress={() => void resume.mutate(task.name)}
      />
    );
  }

  // Primary follows the timer: running → Pause, paused → Resume, not started → Start.
  return (
    <View style={{ flexDirection: "row", gap: space.md }}>
      {state === "running" ? (
        <Button
          label={en.task.pause}
          icon={Pause}
          size="lg"
          variant="primary"
          style={{ flex: 1 }}
          loading={pause.pending}
          onPress={() => void pause.mutate(task.name)}
        />
      ) : (
        <Button
          label={state === "paused" ? en.task.resumeTimer : en.task.start}
          icon={Play}
          size="lg"
          variant="primary"
          style={{ flex: 1 }}
          loading={start.pending}
          onPress={() => void start.mutate(task.name)}
        />
      )}
      <Button
        label={en.task.complete}
        icon={CircleCheck}
        size="lg"
        variant="secondary"
        style={{ flex: 1 }}
        onPress={() => {
          setOpened((n) => n + 1);
          setCompleting(true);
        }}
      />
      <CompleteSheet
        key={opened}
        open={completing}
        onClose={() => setCompleting(false)}
        pending={complete.pending}
        subject={task.subject}
        onSubmit={(h, note) => void complete.mutate(task.name, h, note)}
      />
    </View>
  );
}
