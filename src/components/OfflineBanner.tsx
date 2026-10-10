import { WifiOff } from "@/components/icons";
import { View } from "react-native";

import { en } from "@/i18n/en";
import { useSession } from "@/lib/session";
import { useTheme } from "@/theme/ThemeProvider";
import { space } from "@/theme/tokens";

import { Text } from "./Text";

export function OfflineBanner() {
  const { network } = useSession();
  const { colors } = useTheme();
  if (network !== "offline") return null;
  return (
    <View
      accessibilityRole="alert"
      accessibilityLiveRegion="polite"
      style={{
        flexDirection: "row",
        alignItems: "center",
        gap: space.sm,
        paddingHorizontal: space.lg,
        paddingVertical: space.sm,
        backgroundColor: colors.warningSoft,
        borderBottomWidth: 1,
        borderBottomColor: colors.outline2,
      }}
    >
      <WifiOff size={16} color={colors.warning} />
      <Text variant="small" color="warning" weight="medium" style={{ flex: 1 }}>
        {en.states.offlineBanner}
      </Text>
    </View>
  );
}
