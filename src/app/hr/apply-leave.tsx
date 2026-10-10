import { useRouter } from "expo-router";
import { useState } from "react";
import { View } from "react-native";

import { applyLeave, getLeaveApprover, getLeaveBalances } from "@/api/ess";
import {
  Avatar,
  Button,
  Card,
  DateField,
  Group,
  QueryView,
  Screen,
  SelectField,
  SwitchRow,
  Text,
  TextField,
  useToast,
} from "@/components";
import { en } from "@/i18n/en";
import { day } from "@/lib/clock";
import { writeErrorMessage } from "@/lib/errors";
import { dayCount } from "@/lib/format";
import { useMutation, useQuery } from "@/lib/query";
import { space } from "@/theme/tokens";

export default function ApplyLeave() {
  const router = useRouter();
  const toast = useToast();
  const balances = useQuery("ess:leave-balances", getLeaveBalances);
  const approver = useQuery("ess:leave-approver", getLeaveApprover);

  const [type, setType] = useState<string | null>("Casual Leave");
  const [from, setFrom] = useState<string | null>(day(3));
  const [to, setTo] = useState<string | null>(day(3));
  const [halfDay, setHalfDay] = useState(false);
  const [reason, setReason] = useState("");
  const [touched, setTouched] = useState(false);

  const singleDay = !!from && from === to;
  const days = from && to ? dayCount(from, to, halfDay && singleDay) : 0;
  const balance = balances.data?.find((b) => b.leave_type === type)?.balance ?? 0;
  const dateError = from && to && to < from ? en.leave.toBeforeFrom : undefined;
  const balanceError = days > balance && !dateError ? en.leave.notEnough(balance) : undefined;
  const missing = !type || !from || !to;

  const submit = useMutation(applyLeave, {
    invalidates: ["ess:leave-apps"],
    onSuccess: () => {
      toast(en.leave.submitted, "success");
      router.back();
    },
    onError: (e) => toast(writeErrorMessage(e), "danger"),
  });

  return (
    <Screen
      back
      title={en.leave.applyTitle}
      footer={
        <Button
          label={en.leave.submit}
          variant="primary"
          size="lg"
          full
          loading={submit.pending}
          onPress={() => {
            setTouched(true);
            if (missing || dateError || balanceError || !type || !from || !to) return;
            void submit.mutate({ leave_type: type, from_date: from, to_date: to, half_day: halfDay && singleDay, reason });
          }}
        />
      }
    >
      <QueryView query={balances}>
        {(list) => (
          <View style={{ gap: space.lg }}>
            <SelectField
              label={en.leave.type}
              value={type}
              placeholder={en.leave.type}
              error={touched && !type ? en.common.required : undefined}
              options={list.map((b) => ({
                value: b.leave_type,
                label: b.leave_type,
                description: `${b.balance} ${en.leave.left}`,
              }))}
              onChange={setType}
            />
            <View style={{ flexDirection: "row", gap: space.md }}>
              <DateField
                label={en.leave.from}
                value={from}
                minDate={day(0)}
                onChange={(d) => {
                  setFrom(d);
                  if (!to || to < d) setTo(d);
                }}
              />
              <DateField label={en.leave.to} value={to} minDate={from ?? day(0)} onChange={setTo} error={dateError} />
            </View>
            <Group>
              <SwitchRow
                label={en.leave.halfDay}
                help={en.leave.halfDayHelp}
                value={halfDay && singleDay}
                onChange={setHalfDay}
                disabled={!singleDay}
              />
            </Group>
            {type && days > 0 ? (
              <Text variant="small" color={balanceError ? "danger" : "ink6"} accessibilityLiveRegion="polite">
                <Text variant="small" mono weight="medium" color={balanceError ? "danger" : "ink9"}>
                  {en.leave.days(days)}
                </Text>
                {"  ·  "}
                {balanceError ?? en.leave.after(type, balance - days)}
              </Text>
            ) : null}
            <TextField
              label={en.leave.reason}
              optional
              value={reason}
              onChangeText={setReason}
              placeholder={en.leave.reasonPlaceholder}
              multiline
            />
            <View style={{ gap: space.xs }}>
              <Text variant="small" weight="medium" color="ink7">
                {en.leave.approver}
              </Text>
              <Card>
                <View style={{ flexDirection: "row", alignItems: "center", gap: space.md }}>
                  <Avatar name={approver.data ?? "?"} size={36} />
                  <View style={{ flex: 1 }}>
                    <Text variant="body" weight="medium">
                      {approver.data ?? "…"}
                    </Text>
                    <Text variant="small" color="ink5">
                      {en.leave.approverHelp}
                    </Text>
                  </View>
                </View>
              </Card>
            </View>
          </View>
        )}
      </QueryView>
    </Screen>
  );
}
