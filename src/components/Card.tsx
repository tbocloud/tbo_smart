import type { ReactNode } from "react";
import { View, type StyleProp, type ViewStyle } from "react-native";

import { useTheme } from "@/theme/ThemeProvider";
import { radius, space } from "@/theme/tokens";

import { Pressable } from "./Pressable";

type Props = {
  children: ReactNode;
  onPress?: () => void;
  accessibilityLabel?: string;
  accessibilityHint?: string;
  padded?: boolean;
  /** Left rule in a tone colour, e.g. overdue work (always paired with a label). */
  accent?: string;
  style?: StyleProp<ViewStyle>;
};

export function Card({ children, onPress, accessibilityLabel, accessibilityHint, padded = true, accent, style }: Props) {
  const { colors } = useTheme();
  const base: ViewStyle = {
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.outline2,
    padding: padded ? space.lg : 0,
    overflow: "hidden",
    borderLeftWidth: accent ? 3 : 1,
    borderLeftColor: accent ?? colors.outline2,
  };
  if (!onPress) return <View style={[base, style]}>{children}</View>;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel}
      accessibilityHint={accessibilityHint}
      onPress={onPress}
      ringRadius={radius.lg}
      style={({ pressed }) => [base, pressed && { backgroundColor: colors.surfaceSunken }, style]}
    >
      {children}
    </Pressable>
  );
}
