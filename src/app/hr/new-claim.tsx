import { useRouter } from "expo-router";
import { Camera, FileText, X } from "@/components/icons";
import { useState } from "react";
import { View } from "react-native";

import { createClaim, getExpenseTypes } from "@/api/ess";
import {
  Button,
  DateField,
  IconButton,
  QueryView,
  Screen,
  SelectField,
  Text,
  TextField,
  useToast,
} from "@/components";
import { en } from "@/i18n/en";
import { day } from "@/lib/clock";
import { writeErrorMessage } from "@/lib/errors";
import { useMutation, useQuery } from "@/lib/query";
import { useTheme } from "@/theme/ThemeProvider";
import { radius, space } from "@/theme/tokens";

export default function NewClaim() {
  const router = useRouter();
  const toast = useToast();
  const { colors } = useTheme();
  const types = useQuery("ess:expense-types", getExpenseTypes);
  const [type, setType] = useState<string | null>(null);
  const [amount, setAmount] = useState("");
  const [date, setDate] = useState<string | null>(day(0));
  const [description, setDescription] = useState("");
  const [receipt, setReceipt] = useState<string | null>(null);
  const [touched, setTouched] = useState(false);

  const value = Number(amount.replace(/,/g, ""));
  const amountError = touched && (!amount || Number.isNaN(value) || value <= 0) ? en.expenses.amountInvalid : undefined;
  const typeError = touched && !type ? en.common.required : undefined;

  const submit = useMutation(createClaim, {
    invalidates: ["ess:expenses"],
    onSuccess: () => {
      toast(en.expenses.submitted, "success");
      router.back();
    },
    onError: (e) => toast(writeErrorMessage(e), "danger"),
  });

  return (
    <Screen
      back
      title={en.expenses.newClaim}
      footer={
        <Button
          label={en.expenses.submit}
          variant="primary"
          size="lg"
          full
          loading={submit.pending}
          onPress={() => {
            setTouched(true);
            if (!type || !date || Number.isNaN(value) || value <= 0) return;
            void submit.mutate({ expense_type: type, amount: value, date, description, receipt: receipt ?? undefined });
          }}
        />
      }
    >
      <QueryView query={types}>
        {(list) => (
          <View style={{ gap: space.lg }}>
            <SelectField
              label={en.expenses.type}
              value={type}
              placeholder={en.expenses.type}
              options={list.map((t) => ({ value: t, label: t }))}
              onChange={setType}
              error={typeError}
            />
            <View style={{ flexDirection: "row", gap: space.md }}>
              <View style={{ flex: 1 }}>
                <TextField
                  label={en.expenses.amount}
                  value={amount}
                  onChangeText={setAmount}
                  keyboardType="decimal-pad"
                  placeholder="0"
                  mono
                  error={amountError}
                />
              </View>
              <DateField label={en.expenses.date} value={date} onChange={setDate} />
            </View>
            <TextField
              label={en.expenses.description}
              optional
              value={description}
              onChangeText={setDescription}
              placeholder={en.expenses.descriptionPlaceholder}
              multiline
            />
            <View style={{ gap: space.xs }}>
              <Text variant="small" weight="medium" color="ink7">
                {en.expenses.receipt}
              </Text>
              {receipt ? (
                <View
                  style={{
                    flexDirection: "row",
                    alignItems: "center",
                    gap: space.md,
                    paddingLeft: space.md,
                    borderRadius: radius.md,
                    borderWidth: 1,
                    borderColor: colors.outline3,
                    backgroundColor: colors.surface,
                  }}
                >
                  <FileText size={18} color={colors.ink5} />
                  <Text variant="body" style={{ flex: 1 }} numberOfLines={1}>
                    {receipt}
                  </Text>
                  <IconButton icon={X} label={en.a11y.remove(receipt)} onPress={() => setReceipt(null)} />
                </View>
              ) : (
                <Button label={en.expenses.addReceipt} icon={Camera} onPress={() => setReceipt("bill-1010.jpg")} full />
              )}
              <Text variant="small" color="ink5">
                {en.expenses.receiptHelp}
              </Text>
            </View>
          </View>
        )}
      </QueryView>
    </Screen>
  );
}
