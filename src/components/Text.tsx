import { Text as RNText, type TextProps, type TextStyle } from "react-native";

import { useTheme } from "@/theme/ThemeProvider";
import { fonts, type, type ColorTokens, type TypeVariant } from "@/theme/tokens";

export type TextColor = Extract<
  keyof ColorTokens,
  | "ink9" | "ink7" | "ink6" | "ink5" | "ink4" | "inkInverse"
  | "brand" | "brandInk" | "onBrand"
  | "success" | "warning" | "danger" | "info" | "champion"
>;

type Props = TextProps & {
  variant?: TypeVariant;
  weight?: "regular" | "medium" | "semibold";
  color?: TextColor;
  /** Geist Mono with tabular figures: IDs, emails, counts, times, money. */
  mono?: boolean;
};

export function Text({ variant = "body", weight = "regular", color = "ink9", mono, style, ...rest }: Props) {
  const { colors } = useTheme();
  const family = mono
    ? weight === "regular"
      ? fonts.mono
      : fonts.monoMedium
    : weight === "semibold"
      ? fonts.semibold
      : weight === "medium"
        ? fonts.medium
        : fonts.regular;
  const base: TextStyle = {
    ...type[variant],
    fontFamily: family,
    color: colors[color],
    fontVariant: mono ? ["tabular-nums"] : undefined,
  };
  return <RNText {...rest} style={[base, style]} maxFontSizeMultiplier={1.6} />;
}
