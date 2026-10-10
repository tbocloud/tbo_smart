import { useRouter } from "expo-router";
import { CalendarPlus, TreePalm } from "@/components/icons";
import { View } from "react-native";

import { cancelLeave, getLeaveApplications, getLeaveBalances } from "@/api/ess";
import type { LeaveApplication } from "@/api/types";
import {
  Button,
  Card,
  EmptyState,
  Group,
  QueryView,
  Screen,
  SectionHeader,
  StatusPill,
  Text,
  useToast,
} from "@/components";
import { en } from "@/i18n/en";
import { writeErrorMessage } from "@/lib/errors";
import { dayLabel } from "@/lib/format";
import { useMutation, useQuery } from "@/lib/query";
import { useTheme } from "@/theme/ThemeProvider";
import { space } from "@/theme/tokens";

export default function Leave() {
  const router = useRouter();
  const { colors } = useTheme();
  const balances = useQuery("ess:leave-balances", getLeaveBalances);
  const apps = useQuery("ess:leave-apps", getLeaveApplications);

  return (
    <Screen
      back
      title={en.leave.title}
      onRefresh={() => {
        balances.refresh();
        apps.refresh();
      }}
      refreshing={apps.refreshing}
      footer={
        <Button
          label={en.leave.apply}
          icon={CalendarPlus}
          variant="primary"
          size="lg"
          full
          onPress={() => router.push("/hr/apply-leave")}
        />
      }
    >
      <SectionHeader title={en.leave.balance} />
      <QueryView query={balances} compact>
        {(list) => (
          <Group>
            {list.map((b, i) => (
              <View
                key={b.leave_type}
                accessible
                accessibilityLabel={`${b.leave_type}: ${en.leave.days(b.balance)} left, ${en.leave.of(b.used, b.allocated)}`}
                style={{
                  padding: space.lg,
                  gap: space.sm,
                  borderBottomWidth: i < list.length - 1 ? 1 : 0,
                  borderBottomColor: colors.outline1,
                }}
              >
                <View style={{ flexDirection: "row", alignItems: "baseline" }}>
                  <Text variant="body" weight="medium" style={{ flex: 1 }}>
                    {b.leave_type}
                  </Text>
                  <Text variant="title" mono weight="medium">
                    {b.balance}
                  </Text>
                  <Text variant="small" color="ink5">{` ${en.leave.left}`}</Text>
                </View>
                <View style={{ height: 6, borderRadius: 3, backgroundColor: colors.surfaceRaised, overflow: "hidden" }}>
                  <View
                    style={{
                      width: `${Math.min(100, (b.used / Math.max(b.allocated, 1)) * 100)}%`,
                      height: 6,
                      backgroundColor: colors.ink4,
                    }}
                  />
                </View>
                <Text variant="caption" mono color="ink5">
                  {en.leave.of(b.used, b.allocated)}
                </Text>
              </View>
            ))}
          </Group>
        )}
      </QueryView>

      <SectionHeader title={en.leave.applications} count={apps.data?.length} />
      <QueryView query={apps} compact>
        {(list) =>
          list.length === 0 ? (
            <EmptyState icon={TreePalm} title={en.leave.emptyApps} compact />
          ) : (
            <View style={{ gap: space.md }}>
              {list.map((a) => (
                <ApplicationCard key={a.name} app={a} />
              ))}
            </View>
          )
        }
      </QueryView>
    </Screen>
  );
}

function ApplicationCard({ app }: { app: LeaveApplication }) {
  const toast = useToast();
  const cancel = useMutation(cancelLeave, {
    invalidates: ["ess:leave-apps"],
    onSuccess: () => toast(en.leave.cancelled, "success"),
    onError: (e) => toast(writeErrorMessage(e), "danger"),
  });
  const range = app.from_date === app.to_date ? dayLabel(app.from_date) : `${dayLabel(app.from_date)} – ${dayLabel(app.to_date)}`;
  return (
    <Card>
      <View style={{ gap: space.sm }}>
        <View style={{ flexDirection: "row", alignItems: "center", gap: space.sm }}>
          <Text variant="body" weight="semibold" style={{ flex: 1 }}>
            {app.leave_type}
          </Text>
          <StatusPill status={app.status === "Open" ? "Pending" : app.status} label={en.leave.statuses[app.status]} />
        </View>
        <Text variant="body" color="ink7">
          {range}
          <Text variant="body" color="ink5">{`  ·  ${en.leave.days(app.days)}`}</Text>
        </Text>
        <Text variant="small" color="ink6">
          {app.reason}
        </Text>
        <Text variant="caption" color="ink5">
          {en.leave.approver}: {app.approver} ·{" "}
          <Text variant="caption" mono color="ink5">
            {app.name}
          </Text>
        </Text>
        {app.status === "Open" ? (
          <Button
            label={en.leave.cancel}
            variant="danger"
            loading={cancel.pending}
            onPress={() => void cancel.mutate(app.name)}
            style={{ alignSelf: "flex-start", marginTop: space.xs }}
          />
        ) : null}
      </View>
    </Card>
  );
}
