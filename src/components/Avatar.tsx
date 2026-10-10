import { View } from "react-native";

import { initials } from "@/lib/format";
import { useTheme } from "@/theme/ThemeProvider";

import { Text } from "./Text";

/** Initials on neutral gray; no per-person colours (colour would mean nothing). */
export function Avatar({ name, size = 40 }: { name: string; size?: number }) {
  const { colors } = useTheme();
  return (
    <View
      accessibilityElementsHidden
      importantForAccessibility="no-hide-descendants"
      style={{
        width: size,
        height: size,
        borderRadius: size / 2,
        backgroundColor: colors.surfaceRaised,
        borderWidth: 1,
        borderColor: colors.outline2,
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <Text
        weight="medium"
        color="ink7"
        style={{ fontSize: Math.round(size * 0.38), lineHeight: Math.round(size * 0.5) }}
      >
        {initials(name)}
      </Text>
    </View>
  );
}
