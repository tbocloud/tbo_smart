import { CalendarDays } from "@/components/icons";
import { View } from "react-native";

import { getHolidays } from "@/api/ess";
import type { Holiday } from "@/api/types";
import { Badge, EmptyState, Group, QueryView, Screen, SectionHeader, Text } from "@/components";
import { en } from "@/i18n/en";
import { isoDate, now } from "@/lib/clock";
import { daysFromToday, parse, shortMonth, weekdayShort } from "@/lib/format";
import { useQuery } from "@/lib/query";
import { useTheme } from "@/theme/ThemeProvider";
import { radius, space } from "@/theme/tokens";

export default function Holidays() {
  const holidays = useQuery("ess:holidays", getHolidays);
  const today = isoDate(now());
  return (
    <Screen back title={en.holidays.title} onRefresh={holidays.refresh} refreshing={holidays.refreshing}>
      <QueryView query={holidays}>
        {(list) => {
          if (!list.length) return <EmptyState icon={CalendarDays} title={en.holidays.empty} />;
          const upcoming = list.filter((h) => h.date >= today);
          const past = list.filter((h) => h.date < today).reverse();
          return (
            <View style={{ gap: space.xl }}>
              <View style={{ gap: space.md }}>
                <SectionHeader title={en.holidays.upcoming} count={upcoming.length} />
                <Group>
                  {upcoming.map((h, i) => (
                    <HolidayRow key={h.date} holiday={h} next={i === 0} last={i === upcoming.length - 1} />
                  ))}
                </Group>
              </View>
              <View style={{ gap: space.md }}>
                <SectionHeader title={en.holidays.past} count={past.length} />
                <Group>
                  {past.map((h, i) => (
                    <HolidayRow key={h.date} holiday={h} past last={i === past.length - 1} />
                  ))}
                </Group>
              </View>
            </View>
          );
        }}
      </QueryView>
    </Screen>
  );
}

function HolidayRow({ holiday, next, past, last }: { holiday: Holiday; next?: boolean; past?: boolean; last: boolean }) {
  const { colors } = useTheme();
  const d = parse(holiday.date);
  const inDays = daysFromToday(holiday.date);
  return (
    <View
      accessible
      accessibilityLabel={`${holiday.description}, ${weekdayShort(d.getDay())} ${d.getDate()} ${shortMonth(d.getMonth())}${past ? "" : `, ${en.holidays.inDays(inDays)}`}`}
      style={{
        flexDirection: "row",
        alignItems: "center",
        gap: space.md,
        padding: space.lg,
        borderBottomWidth: last ? 0 : 1,
        borderBottomColor: colors.outline1,
        opacity: past ? 0.7 : 1,
      }}
    >
      <View
        style={{
          width: 48,
          paddingVertical: space.xs,
          borderRadius: radius.md,
          borderWidth: 1,
          borderColor: next ? colors.info : colors.outline2,
          backgroundColor: next ? colors.infoSoft : colors.surface,
          alignItems: "center",
        }}
      >
        <Text variant="caption" weight="medium" color={next ? "info" : "ink5"}>
          {shortMonth(d.getMonth()).toUpperCase()}
        </Text>
        <Text variant="title" mono weight="medium" color={next ? "info" : "ink9"}>
          {d.getDate()}
        </Text>
      </View>
      <View style={{ flex: 1, minWidth: 0 }}>
        <Text variant="body" weight="medium">
          {holiday.description}
        </Text>
        <Text variant="small" color="ink5">
          {weekdayShort(d.getDay())}
        </Text>
      </View>
      {!past ? <Badge label={en.holidays.inDays(inDays)} tone={next ? "info" : "neutral"} /> : null}
    </View>
  );
}
