import { ChevronLeft, ChevronRight } from "@/components/icons";
import { View } from "react-native";

import { en } from "@/i18n/en";
import { isoDate, now } from "@/lib/clock";
import { monthGrid, monthLabel, weekdayShort } from "@/lib/format";
import { useTheme } from "@/theme/ThemeProvider";
import { radius, space, toneColors, type Tone } from "@/theme/tokens";

import { IconButton } from "./IconButton";
import { Pressable } from "./Pressable";
import { Text } from "./Text";

export type DayMark = { tone: Tone; label: string };

type Props = {
  year: number;
  month: number;
  onMonthChange: (year: number, month: number) => void;
  /** Status per ISO date, drawn as a tinted cell plus a dot (never colour alone: the legend names it). */
  marks?: Record<string, DayMark>;
  selected?: string | null;
  onSelect?: (date: string) => void;
  minDate?: string;
};

export function MonthCalendar({ year, month, onMonthChange, marks = {}, selected, onSelect, minDate }: Props) {
  const { colors } = useTheme();
  const today = isoDate(now());
  const cells = monthGrid(year, month);
  const shift = (delta: number) => {
    const d = new Date(year, month + delta, 1);
    onMonthChange(d.getFullYear(), d.getMonth());
  };
  return (
    <View style={{ gap: space.sm }}>
      <View style={{ flexDirection: "row", alignItems: "center" }}>
        <IconButton icon={ChevronLeft} label={en.attendance.prev} onPress={() => shift(-1)} />
        <Text variant="title" weight="semibold" style={{ flex: 1, textAlign: "center" }} accessibilityRole="header">
          {monthLabel(year, month)}
        </Text>
        <IconButton icon={ChevronRight} label={en.attendance.next} onPress={() => shift(1)} />
      </View>
      <View style={{ flexDirection: "row" }}>
        {[1, 2, 3, 4, 5, 6, 0].map((d) => (
          <Text key={d} variant="caption" color="ink5" style={{ flex: 1, textAlign: "center" }}>
            {weekdayShort(d).slice(0, 2)}
          </Text>
        ))}
      </View>
      <View style={{ flexDirection: "row", flexWrap: "wrap" }}>
        {cells.map((date, i) => {
          if (!date) return <View key={`pad-${i}`} style={{ width: `${100 / 7}%`, height: 48 }} />;
          const mark = marks[date];
          const tone = mark ? toneColors(colors, mark.tone) : null;
          const isSelected = date === selected;
          const isToday = date === today;
          const disabled = !!minDate && date < minDate;
          const dayNum = Number(date.slice(8));
          const cell = (
            <View
              style={{
                width: 40,
                height: 40,
                borderRadius: radius.md,
                alignItems: "center",
                justifyContent: "center",
                backgroundColor: isSelected ? colors.brand : tone ? tone.bg : "transparent",
                borderWidth: isToday && !isSelected ? 1.5 : 0,
                borderColor: colors.brand,
              }}
            >
              <Text
                variant="body"
                mono
                weight={isToday || isSelected ? "medium" : "regular"}
                style={{ color: isSelected ? colors.onBrand : disabled ? colors.ink4 : tone ? tone.fg : colors.ink9 }}
              >
                {dayNum}
              </Text>
            </View>
          );
          const a11y = `${dayNum} ${monthLabel(year, month)}${mark ? `, ${mark.label}` : ""}${isToday ? `, ${en.a11y.today}` : ""}`;
          return (
            <View key={date} style={{ width: `${100 / 7}%`, height: 48, alignItems: "center", justifyContent: "center" }}>
              {onSelect ? (
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel={a11y}
                  accessibilityState={{ selected: isSelected, disabled }}
                  disabled={disabled}
                  onPress={() => onSelect(date)}
                  ringRadius={radius.md}
                  style={{ width: 44, height: 44, alignItems: "center", justifyContent: "center" }}
                >
                  {cell}
                </Pressable>
              ) : (
                <View accessible accessibilityLabel={a11y}>
                  {cell}
                </View>
              )}
            </View>
          );
        })}
      </View>
    </View>
  );
}
