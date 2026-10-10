import { View } from "react-native";

import { space } from "@/theme/tokens";

import { Pressable } from "./Pressable";
import { Text } from "./Text";

type Props = {
  title: string;
  count?: number;
  action?: { label: string; onPress: () => void };
};

export function SectionHeader({ title, count, action }: Props) {
  return (
    <View
      style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", minHeight: 32 }}
      accessibilityRole="header"
    >
      <View style={{ flexDirection: "row", alignItems: "baseline", gap: space.sm }}>
        <Text variant="title" weight="semibold">
          {title}
        </Text>
        {count !== undefined ? (
          <Text variant="small" mono color="ink5">
            {count}
          </Text>
        ) : null}
      </View>
      {action ? (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={`${action.label}: ${title}`}
          onPress={action.onPress}
          hitSlop={8}
          style={{ minHeight: 40, justifyContent: "center", paddingHorizontal: 4 }}
        >
          <Text variant="small" weight="medium" color="brandInk">
            {action.label}
          </Text>
        </Pressable>
      ) : null}
    </View>
  );
}
