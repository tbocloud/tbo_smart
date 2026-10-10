import { useState, type ReactNode } from "react";
import { Platform, Pressable as RNPressable, type PressableProps, type StyleProp, type ViewStyle } from "react-native";

import { useTheme } from "@/theme/ThemeProvider";

type Props = Omit<PressableProps, "style" | "children"> & {
  style?: StyleProp<ViewStyle> | ((state: { pressed: boolean }) => StyleProp<ViewStyle>);
  children?: ReactNode;
  /** Ring radius should match the element's own corner radius. */
  ringRadius?: number;
};

/**
 * Pressable with a visible keyboard focus ring (web and hardware keyboards),
 * since a focus outline is non-negotiable (guidelines §16).
 */
export function Pressable({ style, onFocus, onBlur, ringRadius = 8, children, ...rest }: Props) {
  const { colors } = useTheme();
  const [focused, setFocused] = useState(false);
  return (
    <RNPressable
      {...rest}
      onFocus={(e) => {
        // On web only keyboard focus (:focus-visible) shows the ring, not a tap or click.
        const target = e.target as unknown as { matches?: (q: string) => boolean };
        setFocused(Platform.OS !== "web" || !!target.matches?.(":focus-visible"));
        onFocus?.(e);
      }}
      onBlur={(e) => {
        setFocused(false);
        onBlur?.(e);
      }}
      style={(state) => [
        typeof style === "function" ? style(state) : style,
        focused && {
          outlineColor: colors.brand,
          outlineWidth: 2,
          outlineStyle: "solid",
          outlineOffset: 2,
          borderRadius: ringRadius,
        },
      ]}
    >
      {children}
    </RNPressable>
  );
}
