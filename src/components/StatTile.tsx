import { ArrowDown, ArrowUp, type LucideIcon } from "@/components/icons";
import { View } from "react-native";

import { useTheme } from "@/theme/ThemeProvider";
import { space, type Tone } from "@/theme/tokens";

import { Card } from "./Card";
import { Text } from "./Text";
import { ToneIcon } from "./ToneIcon";

type Props = {
  label: string;
  value: string | number;
  icon: LucideIcon;
  /** Only set when the number has a meaning (overdue → danger, done → success). */
  tone?: Tone;
  delta?: { text: string; direction: "up" | "down"; tone: Tone };
  onPress?: () => void;
  hint?: string;
};

export function StatTile({ label, value, icon, tone = "neutral", delta, onPress, hint }: Props) {
  const { colors } = useTheme();
  const valueColor = tone === "danger" && Number(value) > 0 ? "danger" : "ink9";
  const deltaColor = delta?.tone === "success" ? "success" : delta?.tone === "danger" ? "danger" : "ink6";
  const Arrow = delta?.direction === "down" ? ArrowDown : ArrowUp;
  return (
    <Card
      onPress={onPress}
      accessibilityLabel={`${label}: ${value}${delta ? `, ${delta.text}` : ""}`}
      accessibilityHint={hint}
      style={{ flex: 1, minWidth: 0 }}
    >
      <View style={{ gap: space.md }}>
        <ToneIcon icon={icon} tone={tone} size={32} />
        <View style={{ gap: 2 }}>
          <Text variant="h1" mono weight="medium" color={valueColor}>
            {value}
          </Text>
          <Text variant="small" color="ink6" numberOfLines={2}>
            {label}
          </Text>
        </View>
        {delta ? (
          <View style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
            <Arrow size={14} color={colors[deltaColor]} />
            <Text variant="caption" color={deltaColor}>
              {delta.text}
            </Text>
          </View>
        ) : null}
      </View>
    </Card>
  );
}
