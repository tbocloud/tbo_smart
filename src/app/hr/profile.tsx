import { View } from "react-native";

import { getEmployee } from "@/api/ess";
import { Avatar, Group, QueryView, Screen, Text } from "@/components";
import { en } from "@/i18n/en";
import { longDate } from "@/lib/format";
import { useQuery } from "@/lib/query";
import { useTheme } from "@/theme/ThemeProvider";
import { space } from "@/theme/tokens";

export default function Profile() {
  const employee = useQuery("ess:employee", getEmployee);
  return (
    <Screen back title={en.profile.title}>
      <QueryView query={employee}>
        {(e) => (
          <View style={{ gap: space.xl }}>
            <View style={{ alignItems: "center", gap: space.sm, paddingTop: space.md }}>
              <Avatar name={e.employee_name} size={72} />
              <Text variant="h2" weight="semibold">
                {e.employee_name}
              </Text>
              <Text variant="body" color="ink6">
                {e.designation} · {e.department}
              </Text>
            </View>
            <Group>
              <Field label={en.profile.employeeId} value={e.name} mono />
              <Field label={en.profile.reportsTo} value={e.reports_to} />
              <Field label={en.profile.department} value={e.department} />
              <Field label={en.profile.company} value={e.company} />
              <Field label={en.profile.branch} value={e.branch} />
              <Field label={en.profile.joined} value={longDate(e.date_of_joining)} />
              <Field label={en.profile.email} value={e.user} mono />
              <Field label={en.profile.phone} value={e.phone} mono last />
            </Group>
            <Text variant="small" color="ink5" style={{ textAlign: "center" }}>
              {en.profile.readOnly}
            </Text>
          </View>
        )}
      </QueryView>
    </Screen>
  );
}

function Field({ label, value, mono, last }: { label: string; value: string; mono?: boolean; last?: boolean }) {
  const { colors } = useTheme();
  return (
    <View
      accessible
      accessibilityLabel={`${label}: ${value}`}
      style={{
        flexDirection: "row",
        alignItems: "center",
        gap: space.md,
        minHeight: 52,
        paddingHorizontal: space.lg,
        paddingVertical: space.sm,
        borderBottomWidth: last ? 0 : 1,
        borderBottomColor: colors.outline1,
      }}
    >
      <Text variant="body" color="ink6" style={{ width: 110 }}>
        {label}
      </Text>
      <Text variant="body" mono={mono} style={{ flex: 1, textAlign: "right" }} selectable>
        {value}
      </Text>
    </View>
  );
}
