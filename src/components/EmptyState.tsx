import type { LucideIcon } from "@/components/icons";
import { View } from "react-native";

import { space, type Tone } from "@/theme/tokens";

import { Button } from "./Button";
import { Text } from "./Text";
import { ToneIcon } from "./ToneIcon";

type Props = {
  icon: LucideIcon;
  title: string;
  body?: string;
  tone?: Tone;
  action?: { label: string; onPress: () => void; variant?: "primary" | "secondary"; loading?: boolean };
  compact?: boolean;
};

/** Says what's happening, why, and the one next step (guidelines §12). */
export function EmptyState({ icon, title, body, tone = "neutral", action, compact }: Props) {
  return (
    <View
      style={{
        alignItems: "center",
        paddingVertical: compact ? space.xl : space.xxxl,
        paddingHorizontal: space.xl,
        gap: space.md,
      }}
      accessibilityLiveRegion="polite"
    >
      <ToneIcon icon={icon} tone={tone} size={48} />
      <View style={{ gap: space.xs, alignItems: "center" }}>
        <Text variant="title" weight="semibold" style={{ textAlign: "center" }}>
          {title}
        </Text>
        {body ? (
          <Text variant="body" color="ink6" style={{ textAlign: "center", maxWidth: 320 }}>
            {body}
          </Text>
        ) : null}
      </View>
      {action ? (
        <Button
          label={action.label}
          onPress={action.onPress}
          variant={action.variant ?? "secondary"}
          loading={action.loading}
          style={{ marginTop: space.sm }}
        />
      ) : null}
    </View>
  );
}
