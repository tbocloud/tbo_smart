import { useRouter } from "expo-router";
import { Paperclip, Plus, Receipt } from "@/components/icons";
import { View } from "react-native";

import { getExpenseClaims } from "@/api/ess";
import { Button, Card, EmptyState, QueryView, Screen, StatusPill, Text } from "@/components";
import { en } from "@/i18n/en";
import { isoDate, now } from "@/lib/clock";
import { dayLabel, rupees } from "@/lib/format";
import { useQuery } from "@/lib/query";
import { useTheme } from "@/theme/ThemeProvider";
import { space } from "@/theme/tokens";

export default function Expenses() {
  const router = useRouter();
  const { colors } = useTheme();
  const claims = useQuery("ess:expenses", getExpenseClaims);
  const footer = (
    <Button
      label={en.expenses.newClaim}
      icon={Plus}
      variant="primary"
      size="lg"
      full
      onPress={() => router.push("/hr/new-claim")}
    />
  );
  return (
    <Screen back title={en.expenses.title} onRefresh={claims.refresh} refreshing={claims.refreshing} footer={footer}>
      <QueryView query={claims}>
        {(list) => {
          if (!list.length) return <EmptyState icon={Receipt} title={en.expenses.emptyTitle} body={en.expenses.emptyBody} />;
          const pending = list.filter((c) => c.status === "Pending").reduce((a, c) => a + c.amount, 0);
          const approved = list
            .filter((c) => (c.status === "Approved" || c.status === "Paid") && c.date.startsWith(isoDate(now()).slice(0, 7)))
            .reduce((a, c) => a + c.amount, 0);
          return (
            <View style={{ gap: space.lg }}>
              <View style={{ flexDirection: "row", gap: space.md }}>
                <Card style={{ flex: 1 }}>
                  <Text variant="small" color="ink6">
                    {en.expenses.pending}
                  </Text>
                  <Text variant="h2" mono weight="medium">
                    {rupees(pending)}
                  </Text>
                </Card>
                <Card style={{ flex: 1 }}>
                  <Text variant="small" color="ink6">
                    {en.expenses.approved}
                  </Text>
                  <Text variant="h2" mono weight="medium">
                    {rupees(approved)}
                  </Text>
                </Card>
              </View>
              {list.map((c) => (
                <Card key={c.name} accessibilityLabel={`${c.expense_type}, ${rupees(c.amount)}, ${c.status}`}>
                  <View style={{ gap: space.sm }}>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: space.sm }}>
                      <Text variant="body" weight="semibold" style={{ flex: 1 }}>
                        {c.expense_type}
                      </Text>
                      <Text variant="title" mono weight="medium">
                        {rupees(c.amount)}
                      </Text>
                    </View>
                    <Text variant="small" color="ink6">
                      {c.description}
                    </Text>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: space.sm, flexWrap: "wrap" }}>
                      <StatusPill status={c.status} label={en.expenses.statuses[c.status]} />
                      <Text variant="caption" mono color="ink5">
                        {dayLabel(c.date)} · {c.name}
                      </Text>
                      {c.receipt ? <Paperclip size={13} color={colors.ink5} accessibilityLabel={en.expenses.receipt} /> : null}
                    </View>
                  </View>
                </Card>
              ))}
            </View>
          );
        }}
      </QueryView>
    </Screen>
  );
}
