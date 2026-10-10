import { useRouter, type Href } from "expo-router";
import {
  AlarmClock,
  ArrowUpRight,
  Bell,
  CalendarClock,
  CalendarDays,
  ChevronRight,
  CirclePause,
  MessagesSquare,
  TreePalm,
  Sparkles,
} from "@/components/icons";
import { View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { getHolidays, getLeaveBalances, getToday } from "@/api/ess";
import { getActionPlan, getHome, getMe } from "@/api/hub";
import type { HomeCounts } from "@/api/types";
import { useUnreadCount } from "@/features/alerts/useUnreadCount";
import { TodayCard } from "@/features/hr/TodayCard";
import {
  Avatar,
  Card,
  IconButton,
  Pressable,
  QueryView,
  Screen,
  SectionHeader,
  Skeleton,
  StatTile,
  Text,
  ToneIcon,
} from "@/components";
import { en } from "@/i18n/en";
import { isoDate, now } from "@/lib/clock";
import { dayLabel, daysFromToday } from "@/lib/format";
import { useQuery } from "@/lib/query";
import { useTheme } from "@/theme/ThemeProvider";
import { space } from "@/theme/tokens";

export default function Home() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const me = useQuery("hub:me", getMe);
  const home = useQuery("hub:home", getHome);
  const plan = useQuery("hub:plan", getActionPlan);
  const today = useQuery("ess:today", getToday);
  const balances = useQuery("ess:leave-balances", getLeaveBalances);
  const holidays = useQuery("ess:holidays", getHolidays);

  const refresh = () => {
    home.refresh();
    plan.refresh();
    today.refresh();
  };

  const firstName = me.data?.full_name.split(" ")[0] ?? "";
  const unread = useUnreadCount();

  const header = (
    <View
      style={{
        flexDirection: "row",
        alignItems: "center",
        gap: space.md,
        paddingLeft: space.lg,
        paddingRight: space.xs,
        minHeight: 64,
        marginTop: insets.top ? 0 : space.xs,
      }}
    >
      {me.data ? <Avatar name={me.data.full_name} size={44} /> : <Skeleton width={44} height={44} round />}
      <View style={{ flex: 1, minWidth: 0 }}>
        <Text variant="h2" weight="semibold" numberOfLines={1} accessibilityRole="header">
          {firstName ? en.home.greeting(firstName) : " "}
        </Text>
        <Text variant="small" color="ink5">
          {en.home.dateLine}
        </Text>
      </View>
      <IconButton
        icon={Bell}
        label={en.home.notifications(unread)}
        badge={unread}
        onPress={() => router.push("/alerts")}
      />
    </View>
  );

  return (
    <Screen header={header} onRefresh={refresh} refreshing={home.refreshing}>
      <TodayCard today={today} />

      <QueryView query={home} skeleton={<Skeleton height={20} width="80%" />} compact>
        {(h) => (
          <View style={{ gap: space.md }}>
            <SummaryLine counts={h} />
            {h.escalated_to_head > 0 ? (
              <Card
                onPress={() => router.push("/task/TASK-00412")}
                accent={colors.danger}
                accessibilityLabel={`${en.home.followUp(h.escalated_to_head)}. ${en.home.followUpHint}`}
              >
                <View style={{ flexDirection: "row", alignItems: "center", gap: space.md }}>
                  <ToneIcon icon={ArrowUpRight} tone="danger" size={36} />
                  <View style={{ flex: 1, minWidth: 0 }}>
                    <Text variant="body" weight="semibold" color="danger">
                      {en.home.followUp(h.escalated_to_head)}
                    </Text>
                    <Text variant="small" color="ink6">
                      {en.home.followUpHint}
                    </Text>
                  </View>
                  <ChevronRight size={18} color={colors.ink4} />
                </View>
              </Card>
            ) : null}
          </View>
        )}
      </QueryView>

      <Card>
        <View style={{ gap: space.md }}>
          <View style={{ flexDirection: "row", alignItems: "center", gap: space.sm }}>
            <Sparkles size={18} color={colors.ink6} />
            <Text variant="title" weight="semibold" accessibilityRole="header">
              {en.home.planTitle}
            </Text>
          </View>
          <QueryView
            query={plan}
            compact
            skeleton={
              <View style={{ gap: space.sm }}>
                <Skeleton width="90%" />
                <Skeleton width="75%" />
                <Skeleton width="82%" />
              </View>
            }
          >
            {(p) =>
              p.steps.length === 0 ? (
                <Text variant="body" color="ink6">
                  {en.home.planEmpty}
                </Text>
              ) : (
                <View>
                  {p.steps.map((s, i) => (
                    <Pressable
                      key={s.id}
                      accessibilityRole="button"
                      accessibilityLabel={en.a11y.step(i + 1, s.text, s.detail)}
                      onPress={s.route ? () => router.push(s.route as Href) : undefined}
                      style={({ pressed }) => ({
                        flexDirection: "row",
                        gap: space.md,
                        paddingVertical: space.md,
                        marginHorizontal: -space.sm,
                        paddingHorizontal: space.sm,
                        backgroundColor: pressed ? colors.surfaceSunken : "transparent",
                        borderTopWidth: i === 0 ? 0 : 1,
                        borderTopColor: colors.outline1,
                      })}
                    >
                      <View
                        style={{
                          width: 24,
                          height: 24,
                          borderRadius: 12,
                          borderWidth: 1,
                          borderColor: colors.outline3,
                          alignItems: "center",
                          justifyContent: "center",
                          marginTop: 1,
                        }}
                      >
                        <Text variant="caption" mono weight="medium" color="ink6">
                          {i + 1}
                        </Text>
                      </View>
                      <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
                        <Text variant="body" weight="medium">
                          {s.text}
                        </Text>
                        <Text variant="small" color="ink6">
                          {s.detail}
                        </Text>
                      </View>
                      <ChevronRight size={18} color={colors.ink4} style={{ marginTop: 2 }} />
                    </Pressable>
                  ))}
                  <Text variant="caption" color="ink5" style={{ marginTop: space.sm }}>
                    {en.home.planNote}
                  </Text>
                </View>
              )
            }
          </QueryView>
        </View>
      </Card>

      <SectionHeader title={en.home.work} />
      <QueryView
        query={home}
        compact
        skeleton={
          <View style={{ gap: space.md }}>
            <Skeleton height={120} />
            <Skeleton height={120} />
          </View>
        }
      >
        {(h) => (
          <View style={{ gap: space.md }}>
            <View style={{ flexDirection: "row", gap: space.md }}>
              <StatTile
                label={en.home.tiles.awaiting}
                value={h.awaiting_reply}
                icon={MessagesSquare}
                onPress={() => router.push("/work?segment=tickets&view=mine")}
              />
              <StatTile
                label={en.home.tiles.dueToday}
                value={h.tasks_due_today}
                icon={CalendarClock}
                tone="info"
                onPress={() => router.push("/work?segment=tasks&filter=today")}
              />
            </View>
            <View style={{ flexDirection: "row", gap: space.md }}>
              <StatTile
                label={en.home.tiles.overdue}
                value={h.tasks_overdue}
                icon={AlarmClock}
                tone={h.tasks_overdue ? "danger" : "neutral"}
                onPress={() => router.push("/work?segment=tasks&filter=overdue")}
              />
              <StatTile
                label={en.home.tiles.onHold}
                value={h.tasks_on_hold}
                icon={CirclePause}
                onPress={() => router.push("/work?segment=tasks&filter=on_hold")}
              />
            </View>
          </View>
        )}
      </QueryView>

      <View style={{ flexDirection: "row", gap: space.md }}>
        <Card
          style={{ flex: 1, minWidth: 0 }}
          onPress={() => router.push("/hr/leave")}
          accessibilityLabel={`${en.home.leave}: ${balances.data?.reduce((a, b) => a + b.balance, 0) ?? ""} days`}
        >
          <View style={{ gap: space.sm }}>
            <ToneIcon icon={TreePalm} size={32} />
            <Text variant="small" color="ink6">
              {en.home.leave}
            </Text>
            {balances.data ? (
              <>
                <Text variant="h2" mono weight="medium">
                  {en.home.leaveDays(balances.data.reduce((a, b) => a + b.balance, 0))}
                </Text>
                <Text variant="caption" color="ink5" numberOfLines={2}>
                  {balances.data
                    .slice(0, 3)
                    .map((b) => `${b.leave_type.split(" ")[0]} ${b.balance}`)
                    .join(" · ")}
                </Text>
              </>
            ) : balances.error ? (
              <Text variant="small" color="ink5">
                {en.home.hrReconnect}
              </Text>
            ) : (
              <Skeleton width="60%" height={20} />
            )}
          </View>
        </Card>
        <NextHoliday holidays={holidays.data} onPress={() => router.push("/hr/holidays")} />
      </View>
    </Screen>
  );
}

function SummaryLine({ counts }: { counts: HomeCounts }) {
  const parts: { n: number; label: string; danger?: boolean }[] = [
    { n: counts.tasks_overdue, label: en.home.summary.overdue, danger: counts.tasks_overdue > 0 },
    { n: counts.tasks_due_today, label: en.home.summary.dueToday },
    { n: counts.tasks_on_hold, label: en.home.summary.onHold },
    { n: counts.tasks_open, label: en.home.summary.open },
  ];
  return (
    <Text variant="body" color="ink6" accessibilityRole="summary">
      {parts.map((p, i) => (
        <Text key={p.label} variant="body" color={p.danger ? "danger" : "ink7"} weight={p.danger ? "medium" : "regular"}>
          {i > 0 ? <Text color="ink4">{"  ·  "}</Text> : null}
          <Text mono weight="medium" color={p.danger ? "danger" : "ink9"}>
            {p.n}
          </Text>{" "}
          {p.label}
        </Text>
      ))}
    </Text>
  );
}

function NextHoliday({ holidays, onPress }: { holidays?: { date: string; description: string }[]; onPress: () => void }) {
  const today = isoDate(now());
  const next = holidays?.find((h) => h.date >= today);
  const label = next ? `${next.description} · ${dayLabel(next.date)}` : "";
  const inDays = next ? daysFromToday(next.date) : 0;
  return (
    <Card style={{ flex: 1, minWidth: 0 }} onPress={onPress} accessibilityLabel={`${en.home.nextHoliday}: ${label}`}>
      <View style={{ gap: space.sm }}>
        <ToneIcon icon={CalendarDays} tone="info" size={32} />
        <Text variant="small" color="ink6">
          {en.home.nextHoliday}
        </Text>
        {next ? (
          <>
            <Text variant="title" weight="semibold" numberOfLines={2}>
              {next.description}
            </Text>
            <Text variant="caption" color="ink5" mono>
              {dayLabel(next.date)} · {en.holidays.inDays(inDays)}
            </Text>
          </>
        ) : holidays ? (
          <Text variant="small" color="ink5">
            {en.holidays.empty}
          </Text>
        ) : (
          <Skeleton width="70%" height={20} />
        )}
      </View>
    </Card>
  );
}
