import type { ReactNode } from "react";
import { View } from "react-native";

import { useTheme } from "@/theme/ThemeProvider";
import { radius } from "@/theme/tokens";

/** A card that holds ListRows edge to edge (settings-style groups). */
export function Group({ children }: { children: ReactNode }) {
  const { colors } = useTheme();
  return (
    <View
      style={{
        backgroundColor: colors.surface,
        borderRadius: radius.lg,
        borderWidth: 1,
        borderColor: colors.outline2,
        overflow: "hidden",
      }}
    >
      {children}
    </View>
  );
}
