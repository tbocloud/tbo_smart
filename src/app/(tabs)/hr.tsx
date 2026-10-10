import { useRouter } from "expo-router";
import { CalendarCheck, CalendarDays, IdCard, TreePalm, Receipt, Wallet } from "@/components/icons";
import { View } from "react-native";

import {
  getEmployee,
  getExpenseClaims,
  getHolidays,
  getLeaveBalances,
  getSalarySlips,
  getToday,
} from "@/api/ess";
import { Group, ListRow, QueryView, Screen, ToneIcon } from "@/components";
import { TodayCard } from "@/features/hr/TodayCard";
import { en } from "@/i18n/en";
import { isoDate, now } from "@/lib/clock";
import { dayLabel, monthLabel, parse } from "@/lib/format";
import { useQuery } from "@/lib/query";
import { space } from "@/theme/tokens";

export default function HR() {
  const router = useRouter();
  const employee = useQuery("ess:employee", getEmployee);
  const today = useQuery("ess:today", getToday);
  const balances = useQuery("ess:leave-balances", getLeaveBalances);
  const holidays = useQuery("ess:holidays", getHolidays);
  const claims = useQuery("ess:expenses", getExpenseClaims);
  const slips = useQuery("ess:salary", getSalarySlips);

  const todayIso = isoDate(now());
  const nextHoliday = holidays.data?.find((h) => h.date >= todayIso);
  const latestSlip = slips.data?.[0];
  const slipMonth = latestSlip ? parse(`${latestSlip.month}-01`) : null;

  return (
    <Screen
      title={en.hr.title}
      subtitle={employee.data ? `${employee.data.employee_name} · ${employee.data.designation}` : undefined}
      onRefresh={() => {
        today.refresh();
        balances.refresh();
      }}
      refreshing={today.refreshing}
    >
      <QueryView query={employee}>
        {() => (
          <View style={{ gap: space.lg }}>
            <TodayCard today={today} />
            <Group>
              <ListRow
                title={en.hr.attendance}
                subtitle={en.hr.attendanceHint}
                leading={<ToneIcon icon={CalendarCheck} size={36} />}
                onPress={() => router.push("/hr/attendance")}
              />
              <ListRow
                title={en.hr.leave}
                subtitle={balances.data ? en.hr.leaveHint(balances.data.reduce((a, b) => a + b.balance, 0)) : undefined}
                leading={<ToneIcon icon={TreePalm} size={36} />}
                onPress={() => router.push("/hr/leave")}
              />
              <ListRow
                title={en.hr.holidays}
                subtitle={nextHoliday ? en.hr.holidaysHint(nextHoliday.description, dayLabel(nextHoliday.date)) : undefined}
                leading={<ToneIcon icon={CalendarDays} size={36} />}
                onPress={() => router.push("/hr/holidays")}
              />
              <ListRow
                title={en.hr.expenses}
                subtitle={claims.data ? en.hr.expensesHint(claims.data.filter((c) => c.status === "Pending").length) : undefined}
                leading={<ToneIcon icon={Receipt} size={36} />}
                onPress={() => router.push("/hr/expenses")}
              />
              <ListRow
                title={en.hr.salary}
                subtitle={slipMonth ? en.hr.salaryHint(monthLabel(slipMonth.getFullYear(), slipMonth.getMonth())) : undefined}
                leading={<ToneIcon icon={Wallet} size={36} />}
                onPress={() => router.push("/hr/salary")}
              />
              <ListRow
                title={en.hr.profile}
                subtitle={en.hr.profileHint}
                leading={<ToneIcon icon={IdCard} size={36} />}
                onPress={() => router.push("/hr/profile")}
                divider={false}
              />
            </Group>
          </View>
        )}
      </QueryView>
    </Screen>
  );
}
