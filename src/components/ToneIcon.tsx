import type { LucideIcon } from "@/components/icons";
import { View } from "react-native";

import { useTheme } from "@/theme/ThemeProvider";
import { toneColors, type Tone } from "@/theme/tokens";

/**
 * Icon in a soft circle. Tinted only when the tone carries meaning
 * (done = success, overdue = danger, time = info); otherwise neutral gray.
 */
export function ToneIcon({ icon: Icon, tone = "neutral", size = 36 }: { icon: LucideIcon; tone?: Tone; size?: number }) {
  const { colors } = useTheme();
  const c = toneColors(colors, tone);
  return (
    <View
      style={{
        width: size,
        height: size,
        borderRadius: size / 2,
        backgroundColor: c.bg,
        alignItems: "center",
        justifyContent: "center",
      }}
      accessibilityElementsHidden
      importantForAccessibility="no-hide-descendants"
    >
      <Icon size={Math.round(size * 0.5)} color={c.fg} strokeWidth={1.75} />
    </View>
  );
}
