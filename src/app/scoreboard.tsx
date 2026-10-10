import { ArrowUp, Award, Check, CircleCheck, Clock, Gauge, Trophy } from "@/components/icons";
import { View } from "react-native";

import { getScoreboard } from "@/api/hub";
import type { Scoreboard } from "@/api/types";
import { Avatar, Badge, Card, Group, QueryView, Screen, SectionHeader, StatTile, Text } from "@/components";
import { en } from "@/i18n/en";
import { hours } from "@/lib/format";
import { useQuery } from "@/lib/query";
import { useTheme } from "@/theme/ThemeProvider";
import { space, type Tone } from "@/theme/tokens";

/** Same rule as the web Scoreboard: success at 95%+, warning from 80%, danger below. */
function onTimeTone(pct: number): Tone {
  if (pct >= 95) return "success";
  if (pct >= 80) return "warning";
  return "danger";
}

export default function ScoreboardScreen() {
  const board = useQuery("hub:scoreboard", getScoreboard);
  return (
    <Screen
      back
      title={en.scoreboard.title}
      subtitle={board.data ? `${board.data.period_label} · ${board.data.range}` : undefined}
      onRefresh={board.refresh}
      refreshing={board.refreshing}
    >
      <Text variant="small" color="ink6">
        {en.scoreboard.intro}
      </Text>
      <QueryView query={board}>{(b) => <Body board={b} />}</QueryView>
    </Screen>
  );
}

function Body({ board }: { board: Scoreboard }) {
  const { colors } = useTheme();
  const { champion, me } = board;
  return (
    <>
      <View
        accessible
        accessibilityLabel={`${en.scoreboard.champion}, ${board.period_label}: ${champion.full_name}, ${en.scoreboard.score} ${champion.score}`}
        style={{
          backgroundColor: colors.championSoft,
          borderColor: colors.championBorder,
          borderWidth: 1,
          borderRadius: 12,
          padding: space.lg,
          gap: space.lg,
        }}
      >
        <View style={{ flexDirection: "row", alignItems: "flex-start" }}>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 6, flex: 1 }}>
            <Trophy size={16} color={colors.champion} />
            <Text variant="small" weight="semibold" color="champion">
              {en.scoreboard.champion}
            </Text>
            <Text variant="small" color="ink6">
              · {board.period_label}
            </Text>
          </View>
          <View
            style={{
              width: 44,
              height: 44,
              borderRadius: 22,
              borderWidth: 1,
              borderColor: colors.championBorder,
              backgroundColor: colors.surface,
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <Award size={22} color={colors.champion} />
          </View>
        </View>
        <View style={{ flexDirection: "row", alignItems: "center", gap: space.md, marginTop: -space.md }}>
          <Avatar name={champion.full_name} size={52} />
          <View>
            <Text variant="h2" weight="semibold">
              {champion.full_name}
            </Text>
            <Text variant="body" color="ink7">
              {en.scoreboard.score}{" "}
              <Text variant="body" mono weight="medium">
                {champion.score.toFixed(1)}
              </Text>
            </Text>
          </View>
        </View>
        <View style={{ gap: space.sm }}>
          {[en.scoreboard.tasksDone(champion.tasks_done, champion.key_tasks), en.scoreboard.onTime(champion.on_time_pct)].map(
            (line) => (
              <View key={line} style={{ flexDirection: "row", gap: space.sm, alignItems: "center" }}>
                <Check size={16} color={colors.success} />
                <Text variant="body" color="ink7">
                  {line}
                </Text>
              </View>
            ),
          )}
        </View>
      </View>

      <Card>
        <View style={{ flexDirection: "row", alignItems: "center", gap: space.md }}>
          <Avatar name={me.full_name} size={44} />
          <View style={{ flex: 1 }}>
            <Text variant="small" color="ink6">
              {en.scoreboard.rank}
            </Text>
            <Text variant="h2" mono weight="medium">
              {en.scoreboard.rankOf(me.rank, me.of)}
            </Text>
          </View>
          <View style={{ alignItems: "flex-end" }}>
            <Text variant="small" color="ink6">
              {en.scoreboard.score}
            </Text>
            <Text variant="h2" mono weight="medium">
              {me.score.toFixed(1)}
            </Text>
          </View>
        </View>
        {me.delta_rank > 0 ? (
          <View style={{ flexDirection: "row", alignItems: "center", gap: 4, marginTop: space.sm }}>
            <ArrowUp size={14} color={colors.success} />
            <Text variant="small" color="success">
              {en.scoreboard.up(me.delta_rank)}
            </Text>
          </View>
        ) : null}
      </Card>

      <View style={{ flexDirection: "row", gap: space.md }}>
        <StatTile label={en.scoreboard.done} value={me.tasks_done} icon={CircleCheck} tone="success" />
        <StatTile label={en.scoreboard.onTimeLabel} value={`${me.on_time_pct}%`} icon={Gauge} tone={onTimeTone(me.on_time_pct)} />
        <StatTile label={en.scoreboard.hours} value={hours(me.hours)} icon={Clock} tone="info" />
      </View>

      <View style={{ gap: space.md }}>
        <SectionHeader title={en.scoreboard.howScore} />
        <Group>
          {me.breakdown.map((part) => (
            <View
              key={part.label}
              style={{
                flexDirection: "row",
                padding: space.lg,
                borderBottomWidth: 1,
                borderBottomColor: colors.outline1,
              }}
            >
              <Text variant="body" color="ink7" style={{ flex: 1 }}>
                {part.label}
              </Text>
              <Text variant="body" mono>
                {en.scoreboard.points(part.points)}
              </Text>
            </View>
          ))}
          <View style={{ flexDirection: "row", padding: space.lg }}>
            <Text variant="body" weight="semibold" style={{ flex: 1 }}>
              {en.scoreboard.score}
            </Text>
            <Text variant="body" mono weight="medium">
              {me.score.toFixed(1)}
            </Text>
          </View>
        </Group>
      </View>

      <View style={{ gap: space.md }}>
        <SectionHeader title={en.scoreboard.leaders} />
        <Group>
          {board.leaders.map((l, i) => (
            <View
              key={l.full_name}
              accessible
              accessibilityLabel={`#${l.rank} ${l.full_name}, ${en.scoreboard.score} ${l.score}`}
              style={{
                flexDirection: "row",
                alignItems: "center",
                gap: space.md,
                paddingHorizontal: space.lg,
                minHeight: 56,
                borderBottomWidth: i < board.leaders.length - 1 ? 1 : 0,
                borderBottomColor: colors.outline1,
              }}
            >
              <Text variant="body" mono color="ink5" style={{ width: 24 }}>
                {l.rank}
              </Text>
              <Avatar name={l.full_name} size={32} />
              <Text variant="body" weight="medium" style={{ flex: 1 }} numberOfLines={1}>
                {l.full_name}
              </Text>
              {l.full_name === me.full_name ? <Badge label={en.scoreboard.you} /> : null}
              {l.rank === 1 ? <Trophy size={16} color={colors.champion} accessibilityLabel={en.scoreboard.champion} /> : null}
              <Text variant="body" mono weight="medium" style={{ minWidth: 40, textAlign: "right" }}>
                {l.score.toFixed(1)}
              </Text>
            </View>
          ))}
        </Group>
      </View>
    </>
  );
}
