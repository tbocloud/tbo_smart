import { ChevronRight } from "@/components/icons";
import type { ReactNode } from "react";
import { View } from "react-native";

import { useTheme } from "@/theme/ThemeProvider";
import { space } from "@/theme/tokens";

import { Pressable } from "./Pressable";
import { Text } from "./Text";

type Props = {
  title: string;
  subtitle?: string;
  leading?: ReactNode;
  trailing?: ReactNode;
  onPress?: () => void;
  chevron?: boolean;
  divider?: boolean;
  accessibilityLabel?: string;
  /** For rows that toggle or choose rather than navigate. */
  accessibilityRole?: "button" | "radio" | "switch" | "link";
  selected?: boolean;
};

export function ListRow({
  title,
  subtitle,
  leading,
  trailing,
  onPress,
  chevron = !!onPress,
  divider = true,
  accessibilityLabel,
  accessibilityRole = "button",
  selected,
}: Props) {
  const { colors } = useTheme();
  const content = (
    <View
      style={{
        flexDirection: "row",
        alignItems: "center",
        gap: space.md,
        minHeight: 56,
        paddingVertical: space.sm,
        paddingHorizontal: space.lg,
      }}
    >
      {leading}
      <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
        <Text variant="body" weight="medium" numberOfLines={2}>
          {title}
        </Text>
        {subtitle ? (
          <Text variant="small" color="ink5" numberOfLines={2}>
            {subtitle}
          </Text>
        ) : null}
      </View>
      {trailing}
      {chevron ? <ChevronRight size={18} color={colors.ink4} /> : null}
    </View>
  );
  const border = divider ? { borderBottomWidth: 1, borderBottomColor: colors.outline1 } : null;
  if (!onPress) return <View style={border}>{content}</View>;
  return (
    <Pressable
      accessibilityRole={accessibilityRole}
      accessibilityLabel={accessibilityLabel ?? (subtitle ? `${title}, ${subtitle}` : title)}
      accessibilityState={selected !== undefined ? { selected, checked: selected } : undefined}
      onPress={onPress}
      ringRadius={0}
      style={({ pressed }) => [border, pressed && { backgroundColor: colors.surfaceSunken }]}
    >
      {content}
    </Pressable>
  );
}
