import type { LucideIcon } from "@/components/icons";
import { ActivityIndicator, View, type StyleProp, type ViewStyle } from "react-native";

import { useTheme } from "@/theme/ThemeProvider";
import { radius, space, TAP } from "@/theme/tokens";

import { Pressable } from "./Pressable";
import { Text, type TextColor } from "./Text";

type Variant = "primary" | "secondary" | "ghost" | "danger";

type Props = {
  label: string;
  onPress?: () => void;
  variant?: Variant;
  size?: "md" | "lg";
  icon?: LucideIcon;
  loading?: boolean;
  disabled?: boolean;
  full?: boolean;
  accessibilityHint?: string;
  style?: StyleProp<ViewStyle>;
};

export function Button({
  label,
  onPress,
  variant = "secondary",
  size = "md",
  icon: Icon,
  loading,
  disabled,
  full,
  accessibilityHint,
  style,
}: Props) {
  const { colors } = useTheme();
  const inactive = disabled || loading;

  const palette: Record<Variant, { bg: string; pressed: string; fg: TextColor; border?: string }> = {
    primary: { bg: colors.brand, pressed: colors.brandPressed, fg: "onBrand" },
    secondary: { bg: colors.surface, pressed: colors.surfaceRaised, fg: "ink9", border: colors.outline3 },
    ghost: { bg: "transparent", pressed: colors.surfaceRaised, fg: "ink7" },
    danger: { bg: colors.surface, pressed: colors.dangerSoft, fg: "danger", border: colors.outline3 },
  };
  const p = palette[variant];
  const height = size === "lg" ? 56 : TAP;

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityHint={accessibilityHint}
      accessibilityState={{ disabled: !!inactive, busy: !!loading }}
      disabled={inactive}
      onPress={onPress}
      ringRadius={radius.lg}
      style={({ pressed }) => [
        {
          minHeight: height,
          paddingHorizontal: size === "lg" ? space.xl : space.lg,
          borderRadius: radius.lg,
          backgroundColor: pressed ? p.pressed : p.bg,
          borderWidth: p.border ? 1 : 0,
          borderColor: p.border,
          alignItems: "center",
          justifyContent: "center",
          opacity: disabled && !loading ? 0.5 : 1,
          alignSelf: full ? "stretch" : "auto",
        },
        style,
      ]}
    >
      {/* Label stays in layout while loading so the button keeps its width. */}
      <View style={{ flexDirection: "row", alignItems: "center", gap: space.sm, opacity: loading ? 0 : 1 }}>
        {Icon ? <Icon size={size === "lg" ? 20 : 18} color={colors[p.fg]} strokeWidth={2} /> : null}
        <Text variant={size === "lg" ? "title" : "body"} weight="medium" color={p.fg}>
          {label}
        </Text>
      </View>
      {loading ? (
        <ActivityIndicator style={{ position: "absolute" }} color={colors[p.fg]} accessibilityLabel={label} />
      ) : null}
    </Pressable>
  );
}
