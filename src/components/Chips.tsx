import { ScrollView } from "react-native";

import { useTheme } from "@/theme/ThemeProvider";
import { radius, space } from "@/theme/tokens";

import { Pressable } from "./Pressable";
import { Text } from "./Text";

type Chip<T extends string> = { value: T; label: string; count?: number };

/**
 * Secondary filter row under a segmented control. Selected chip is
 * brand-tinted (soft), so it never competes with the brand-filled segment.
 */
export function Chips<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: Chip<T>[];
  value: T;
  onChange: (v: T) => void;
  label: string;
}) {
  const { colors } = useTheme();
  return (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      contentContainerStyle={{ gap: space.sm }}
      accessibilityRole="radiogroup"
      accessibilityLabel={label}
    >
      {options.map((o) => {
        const active = o.value === value;
        return (
          <Pressable
            key={o.value}
            accessibilityRole="radio"
            accessibilityState={{ checked: active }}
            accessibilityLabel={o.count !== undefined ? `${o.label}, ${o.count}` : o.label}
            onPress={() => onChange(o.value)}
            ringRadius={radius.pill}
            hitSlop={{ top: 4, bottom: 4 }}
            style={({ pressed }) => ({
              minHeight: 40,
              paddingHorizontal: space.lg,
              borderRadius: radius.pill,
              flexDirection: "row",
              alignItems: "center",
              gap: 6,
              borderWidth: 1,
              borderColor: active ? colors.brandSoft : colors.outline3,
              backgroundColor: active ? colors.brandSoft : pressed ? colors.surfaceRaised : colors.surface,
            })}
          >
            <Text variant="small" weight="medium" color={active ? "brandInk" : "ink7"}>
              {o.label}
            </Text>
            {o.count !== undefined ? (
              <Text variant="small" mono color={active ? "brandInk" : "ink5"}>
                {o.count}
              </Text>
            ) : null}
          </Pressable>
        );
      })}
    </ScrollView>
  );
}
