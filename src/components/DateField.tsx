import { CalendarDays } from "@/components/icons";
import { useState } from "react";

import { en } from "@/i18n/en";
import { dayLabel, parse } from "@/lib/format";

import { FieldButton } from "./FieldButton";
import { MonthCalendar } from "./MonthCalendar";
import { Sheet } from "./Sheet";

type Props = {
  label: string;
  value: string | null;
  onChange: (iso: string) => void;
  minDate?: string;
  error?: string;
};

/** Date input that opens the same month calendar used by Attendance. */
export function DateField({ label, value, onChange, minDate, error }: Props) {
  const [open, setOpen] = useState(false);
  const base = parse(value ?? minDate ?? "2026-10-10");
  const [ym, setYm] = useState({ year: base.getFullYear(), month: base.getMonth() });
  return (
    <>
      <FieldButton
        label={label}
        value={value ? dayLabel(value) : null}
        placeholder={en.leave.pickDate}
        icon={CalendarDays}
        onPress={() => setOpen(true)}
        error={error}
      />
      <Sheet open={open} onClose={() => setOpen(false)} title={label}>
        <MonthCalendar
          year={ym.year}
          month={ym.month}
          onMonthChange={(year, month) => setYm({ year, month })}
          selected={value}
          minDate={minDate}
          onSelect={(d) => {
            onChange(d);
            setOpen(false);
          }}
        />
      </Sheet>
    </>
  );
}
