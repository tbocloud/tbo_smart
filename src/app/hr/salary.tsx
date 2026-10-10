import { Eye, EyeOff, FileText, Wallet } from "@/components/icons";
import { useState } from "react";
import { View } from "react-native";

import { getSalarySlips } from "@/api/ess";
import { Button, Card, EmptyState, QueryView, Screen, Text, useToast } from "@/components";
import { en } from "@/i18n/en";
import { monthLabel, parse, rupees } from "@/lib/format";
import { useQuery } from "@/lib/query";
import { space } from "@/theme/tokens";

export default function Salary() {
  const toast = useToast();
  const slips = useQuery("ess:salary", getSalarySlips);
  // Hidden by default: people open this in buses and offices.
  const [shown, setShown] = useState(false);
  const money = (n: number) => (shown ? rupees(n) : "₹ ••,•••");

  return (
    <Screen
      back
      title={en.salary.title}
      right={
        <Button
          label={shown ? en.salary.hide : en.salary.show}
          icon={shown ? EyeOff : Eye}
          variant="ghost"
          onPress={() => setShown((v) => !v)}
        />
      }
    >
      {!shown ? (
        <Text variant="small" color="ink5">
          {en.salary.hiddenNote}
        </Text>
      ) : null}
      <QueryView query={slips}>
        {(list) =>
          list.length === 0 ? (
            <EmptyState icon={Wallet} title={en.salary.empty} />
          ) : (
            <View style={{ gap: space.md }}>
              {list.map((s) => {
                const d = parse(`${s.month}-01`);
                return (
                  <Card key={s.name}>
                    <View style={{ gap: space.md }}>
                      <View style={{ flexDirection: "row", alignItems: "baseline" }}>
                        <Text variant="title" weight="semibold" style={{ flex: 1 }}>
                          {monthLabel(d.getFullYear(), d.getMonth())}
                        </Text>
                        <Text variant="title" mono weight="medium" accessibilityLabel={shown ? rupees(s.net_pay) : en.salary.show}>
                          {money(s.net_pay)}
                        </Text>
                      </View>
                      <View style={{ flexDirection: "row", gap: space.lg }}>
                        <View style={{ flex: 1 }}>
                          <Text variant="caption" color="ink5">
                            {en.salary.gross}
                          </Text>
                          <Text variant="body" mono>
                            {money(s.gross)}
                          </Text>
                        </View>
                        <View style={{ flex: 1 }}>
                          <Text variant="caption" color="ink5">
                            {en.salary.deductions}
                          </Text>
                          <Text variant="body" mono>
                            {money(s.deductions)}
                          </Text>
                        </View>
                      </View>
                      <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
                        <Text variant="small" mono color="ink5">
                          {en.salary.days(s.payment_days, s.working_days)}
                        </Text>
                        <Button label={en.salary.view} icon={FileText} onPress={() => toast(en.projects.downloadNote)} />
                      </View>
                    </View>
                  </Card>
                );
              })}
            </View>
          )
        }
      </QueryView>
    </Screen>
  );
}
