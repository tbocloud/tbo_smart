import type { LucideIcon } from "@/components/icons";
import { View } from "react-native";

import { useTheme } from "@/theme/ThemeProvider";
import { radius, TAP } from "@/theme/tokens";

import { Pressable } from "./Pressable";
import { Text } from "./Text";

type Props = {
  icon: LucideIcon;
  label: string;
  onPress?: () => void;
  badge?: number;
  color?: string;
  disabled?: boolean;
};

/** 48×48 hit area around a 22px icon; `label` is required for screen readers. */
export function IconButton({ icon: Icon, label, onPress, badge, color, disabled }: Props) {
  const { colors } = useTheme();
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      onPress={onPress}
      disabled={disabled}
      ringRadius={radius.pill}
      style={({ pressed }) => ({
        width: TAP,
        height: TAP,
        borderRadius: radius.pill,
        alignItems: "center",
        justifyContent: "center",
        backgroundColor: pressed ? colors.surfaceRaised : "transparent",
        opacity: disabled ? 0.4 : 1,
      })}
    >
      <Icon size={22} color={color ?? colors.ink7} strokeWidth={1.75} />
      {badge ? (
        <View
          style={{
            position: "absolute",
            top: 6,
            right: 4,
            minWidth: 18,
            height: 18,
            paddingHorizontal: 4,
            borderRadius: 9,
            backgroundColor: colors.brand,
            alignItems: "center",
            justifyContent: "center",
            borderWidth: 2,
            borderColor: colors.surface,
          }}
        >
          <Text variant="caption" mono weight="medium" color="onBrand" style={{ fontSize: 10, lineHeight: 12 }}>
            {badge > 99 ? "99+" : badge}
          </Text>
        </View>
      ) : null}
    </Pressable>
  );
}
