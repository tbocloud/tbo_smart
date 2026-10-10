import { View } from "react-native";

import { useTheme } from "@/theme/ThemeProvider";
import { radius, TAP } from "@/theme/tokens";

import { Pressable } from "./Pressable";
import { Text } from "./Text";

type Option<T extends string> = { value: T; label: string; count?: number };

type Props<T extends string> = {
  options: Option<T>[];
  value: T;
  onChange: (value: T) => void;
  label: string;
};

/** The active option is brand-filled: the current selection is the accent's job. */
export function SegmentedControl<T extends string>({ options, value, onChange, label }: Props<T>) {
  const { colors } = useTheme();
  return (
    <View
      accessibilityRole="tablist"
      accessibilityLabel={label}
      style={{
        flexDirection: "row",
        backgroundColor: colors.surfaceSunken,
        borderRadius: radius.lg,
        padding: 4,
        gap: 4,
        borderWidth: 1,
        borderColor: colors.outline1,
      }}
    >
      {options.map((o) => {
        const active = o.value === value;
        return (
          <Pressable
            key={o.value}
            accessibilityRole="tab"
            accessibilityState={{ selected: active }}
            accessibilityLabel={o.count !== undefined ? `${o.label}, ${o.count}` : o.label}
            onPress={() => onChange(o.value)}
            ringRadius={radius.md}
            style={({ pressed }) => ({
              flex: 1,
              minHeight: TAP - 8,
              borderRadius: radius.md,
              alignItems: "center",
              justifyContent: "center",
              flexDirection: "row",
              gap: 6,
              backgroundColor: active ? colors.brand : pressed ? colors.surfaceRaised : "transparent",
            })}
          >
            <Text variant="body" weight="medium" color={active ? "onBrand" : "ink6"} numberOfLines={1}>
              {o.label}
            </Text>
            {o.count !== undefined ? (
              <Text variant="small" mono color={active ? "onBrand" : "ink5"}>
                {o.count}
              </Text>
            ) : null}
          </Pressable>
        );
      })}
    </View>
  );
}
