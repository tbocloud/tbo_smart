import { useRouter } from "expo-router";
import { CircleCheck, Clock, LogIn, LogOut, Unplug } from "@/components/icons";
import { View } from "react-native";

import type { Today } from "@/api/types";
import { Button, Card, Skeleton, Text } from "@/components";
import { en } from "@/i18n/en";
import { shiftTime, time } from "@/lib/format";
import type { Query } from "@/lib/query";
import { useSession } from "@/lib/session";
import { useTheme } from "@/theme/ThemeProvider";
import { space } from "@/theme/tokens";

/** Shift, check-in status and the one big Check in / Check out button (Home and HR). */
export function TodayCard({ today }: { today: Query<Today> }) {
  const router = useRouter();
  const { colors } = useTheme();
  const { connect } = useSession();
  const t = today.data;
  const lastIn = t?.checkins.filter((c) => c.log_type === "IN").at(-1);
  const lastOut = t?.checkins.filter((c) => c.log_type === "OUT").at(-1);

  return (
    <Card>
      <View style={{ gap: space.lg }}>
        <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
          <Text variant="title" weight="semibold" accessibilityRole="header">
            {en.home.today}
          </Text>
          {t ? (
            <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
              <Clock size={14} color={colors.ink5} />
              <Text variant="small" mono color="ink6">
                {en.home.shift(shiftTime(t.shift_start), shiftTime(t.shift_end))}
              </Text>
            </View>
          ) : null}
        </View>

        {today.error?.code === "reconnect" && !t ? (
          <View style={{ gap: space.md }}>
            <View style={{ flexDirection: "row", gap: space.sm, alignItems: "center" }}>
              <Unplug size={16} color={colors.ink5} />
              <Text variant="body" color="ink6" style={{ flex: 1 }}>
                {en.home.hrReconnect}
              </Text>
            </View>
            <Button label={`${en.common.reconnect} ${en.sites.ess}`} onPress={() => connect("ess")} full />
          </View>
        ) : !t ? (
          <View style={{ gap: space.md }}>
            <Skeleton width="50%" />
            <Skeleton height={56} />
          </View>
        ) : (
          <>
            <View style={{ flexDirection: "row", alignItems: "center", gap: space.sm }}>
              {t.status === "not_checked_in" ? (
                <Text variant="body" color="ink6">
                  {en.home.notCheckedIn}
                </Text>
              ) : (
                <>
                  <CircleCheck size={18} color={colors.success} />
                  <Text variant="body" color="ink7">
                    {t.status === "checked_out" && lastOut
                      ? en.home.checkedOutAt(time(lastOut.time))
                      : lastIn
                        ? en.home.checkedInAt(time(lastIn.time))
                        : null}
                  </Text>
                </>
              )}
            </View>
            {t.status === "checked_out" ? (
              <Button label={en.home.dayDone} icon={CircleCheck} size="lg" full disabled />
            ) : (
              <Button
                label={t.status === "checked_in" ? en.home.checkOut : en.home.checkIn}
                icon={t.status === "checked_in" ? LogOut : LogIn}
                variant="primary"
                size="lg"
                full
                onPress={() => router.push(t.status === "checked_in" ? "/hr/checkin?type=OUT" : "/hr/checkin?type=IN")}
              />
            )}
          </>
        )}
      </View>
    </Card>
  );
}
