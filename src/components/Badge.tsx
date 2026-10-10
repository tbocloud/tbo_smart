import type { LucideIcon } from "@/components/icons";
import { View } from "react-native";

import { useTheme } from "@/theme/ThemeProvider";
import { radius, toneColors, type Tone } from "@/theme/tokens";

import { Text } from "./Text";

type Props = { label: string; tone?: Tone; icon?: LucideIcon; outline?: boolean };

/** Small label. Colour always comes with text, and an icon where it helps. */
export function Badge({ label, tone = "neutral", icon: Icon, outline }: Props) {
  const { colors } = useTheme();
  const c = toneColors(colors, tone);
  const fg = tone === "neutral" ? colors.ink7 : c.fg;
  return (
    <View
      style={{
        flexDirection: "row",
        alignItems: "center",
        gap: 4,
        paddingHorizontal: 8,
        height: 24,
        borderRadius: radius.sm,
        backgroundColor: outline ? "transparent" : c.bg,
        borderWidth: outline ? 1 : 0,
        borderColor: colors.outline3,
      }}
    >
      {Icon ? <Icon size={13} color={fg} strokeWidth={2} /> : null}
      <Text variant="caption" weight="medium" style={{ color: fg }} numberOfLines={1}>
        {label}
      </Text>
    </View>
  );
}
