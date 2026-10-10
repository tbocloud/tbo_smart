import { Check } from "@/components/icons";
import type { ReactNode } from "react";

import { useTheme } from "@/theme/ThemeProvider";

import { Group } from "./Group";
import { ListRow } from "./ListRow";

type Option<T> = { value: T; label: string; description?: string; leading?: ReactNode };

/** Single-choice list: the selected row carries a brand check (current selection). */
export function OptionList<T extends string>({
  options,
  value,
  onChange,
}: {
  options: Option<T>[];
  value: T | null;
  onChange: (value: T) => void;
}) {
  const { colors } = useTheme();
  return (
    <Group>
      {options.map((o, i) => (
        <ListRow
          key={o.value}
          title={o.label}
          subtitle={o.description}
          leading={o.leading}
          accessibilityRole="radio"
          selected={o.value === value}
          onPress={() => onChange(o.value)}
          chevron={false}
          divider={i < options.length - 1}
          trailing={o.value === value ? <Check size={20} color={colors.brand} strokeWidth={2.25} /> : null}
        />
      ))}
    </Group>
  );
}
