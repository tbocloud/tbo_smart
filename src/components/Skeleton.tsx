import { useEffect } from "react";
import { View, type DimensionValue } from "react-native";
import Animated, {
  useAnimatedStyle,
  useReducedMotion,
  useSharedValue,
  withRepeat,
  withTiming,
} from "react-native-reanimated";

import { en } from "@/i18n/en";
import { useTheme } from "@/theme/ThemeProvider";
import { radius, space } from "@/theme/tokens";

type Props = { width?: DimensionValue; height?: number; round?: boolean };

/** Gentle pulse; static when the user asks for reduced motion. */
export function Skeleton({ width = "100%", height = 14, round }: Props) {
  const { colors } = useTheme();
  const reduced = useReducedMotion();
  const opacity = useSharedValue(1);
  useEffect(() => {
    if (!reduced) opacity.value = withRepeat(withTiming(0.45, { duration: 800 }), -1, true);
  }, [opacity, reduced]);
  const style = useAnimatedStyle(() => ({ opacity: opacity.value }));
  return (
    <Animated.View
      style={[
        {
          width,
          height,
          borderRadius: round ? height / 2 : radius.sm,
          backgroundColor: colors.surfaceStrong,
        },
        style,
      ]}
    />
  );
}

/** Card-shaped placeholders matching the list layout that will load. */
export function SkeletonCards({ count = 3, lines = 3 }: { count?: number; lines?: number }) {
  const { colors } = useTheme();
  return (
    <View style={{ gap: space.md }} accessibilityLabel={en.a11y.loading} accessibilityRole="progressbar">
      {Array.from({ length: count }, (_, i) => (
        <View
          key={i}
          style={{
            backgroundColor: colors.surface,
            borderRadius: radius.lg,
            borderWidth: 1,
            borderColor: colors.outline2,
            padding: space.lg,
            gap: space.sm,
          }}
        >
          <Skeleton width="40%" height={12} />
          <Skeleton width="85%" height={16} />
          {Array.from({ length: Math.max(0, lines - 2) }, (_, j) => (
            <Skeleton key={j} width={j % 2 ? "50%" : "70%"} height={12} />
          ))}
        </View>
      ))}
    </View>
  );
}
