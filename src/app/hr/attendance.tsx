import { LogIn, LogOut } from "@/components/icons";
import { useState } from "react";
import { View } from "react-native";

import { getAttendance, getToday } from "@/api/ess";
import type { AttendanceStatus } from "@/api/types";
import { Card, MonthCalendar, QueryView, Screen, SectionHeader, Skeleton, Text, type DayMark } from "@/components";
import { en } from "@/i18n/en";
import { now } from "@/lib/clock";
import { time } from "@/lib/format";
import { useQuery } from "@/lib/query";
import { useTheme } from "@/theme/ThemeProvider";
import { space, toneColors, type Tone } from "@/theme/tokens";

const TONE: Record<AttendanceStatus, Tone> = {
  Present: "success",
  Absent: "danger",
  "On Leave": "info",
  Holiday: "neutral",
  "Weekly off": "neutral",
};

export default function Attendance() {
  const { colors } = useTheme();
  const [ym, setYm] = useState({ year: now().getFullYear(), month: now().getMonth() });
  const days = useQuery(`ess:attendance:${ym.year}-${ym.month}`, () => getAttendance(ym.year, ym.month));
  const today = useQuery("ess:today", getToday);

  const marks: Record<string, DayMark> = {};
  for (const d of days.data ?? []) {
    if (d.status === "Weekly off") continue;
    marks[d.date] = { tone: TONE[d.status], label: en.attendance.legend[d.status] };
  }
  const count = (s: AttendanceStatus) => (days.data ?? []).filter((d) => d.status === s).length;

  return (
    <Screen back title={en.attendance.title} onRefresh={days.refresh} refreshing={days.refreshing}>
      <Card>
        <MonthCalendar
          year={ym.year}
          month={ym.month}
          onMonthChange={(year, month) => setYm({ year, month })}
          marks={marks}
        />
        <View style={{ flexDirection: "row", flexWrap: "wrap", gap: space.md, marginTop: space.md }}>
          {(["Present", "On Leave", "Absent", "Holiday"] as AttendanceStatus[]).map((s) => {
            const c = toneColors(colors, TONE[s]);
            return (
              <View key={s} style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
                <View style={{ width: 12, height: 12, borderRadius: 3, backgroundColor: c.bg, borderWidth: 1, borderColor: c.fg }} />
                <Text variant="small" color="ink6">
                  {en.attendance.legend[s]}
                </Text>
              </View>
            );
          })}
        </View>
        {days.data ? (
          <Text variant="small" mono color="ink5" style={{ marginTop: space.md }}>
            {en.attendance.summary(count("Present"), count("On Leave"), count("Absent"))}
          </Text>
        ) : null}
      </Card>

      <SectionHeader title={en.attendance.today} />
      <QueryView query={today} skeleton={<Skeleton height={56} />} compact>
        {(t) =>
          t.checkins.length === 0 ? (
            <Card>
              <Text variant="body" color="ink6">
                {en.attendance.noPunches}
              </Text>
            </Card>
          ) : (
            <Card padded={false}>
              {t.checkins.map((c, i) => (
                <View
                  key={c.time + c.log_type}
                  style={{
                    flexDirection: "row",
                    alignItems: "center",
                    gap: space.md,
                    padding: space.lg,
                    borderTopWidth: i ? 1 : 0,
                    borderTopColor: colors.outline1,
                  }}
                >
                  {c.log_type === "IN" ? <LogIn size={18} color={colors.ink6} /> : <LogOut size={18} color={colors.ink6} />}
                  <View style={{ flex: 1 }}>
                    <Text variant="body" weight="medium">
                      {c.log_type === "IN" ? en.attendance.in : en.attendance.out}
                    </Text>
                    <Text variant="small" color="ink5">
                      {c.location}
                    </Text>
                  </View>
                  <Text variant="body" mono weight="medium">
                    {time(c.time)}
                  </Text>
                </View>
              ))}
            </Card>
          )
        }
      </QueryView>
    </Screen>
  );
}
