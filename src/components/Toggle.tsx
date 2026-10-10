import { Platform, Switch } from "react-native";

import { useTheme } from "@/theme/ThemeProvider";

/**
 * Switch in TBO colours. Hidden from screen readers because the row around it
 * carries the label and the switch role.
 */
export function Toggle({ value, onChange, disabled }: { value: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  const { colors } = useTheme();
  // react-native-web paints the "on" thumb with its own teal unless told otherwise.
  const webThumb = Platform.OS === "web" ? ({ activeThumbColor: colors.surface } as object) : {};
  return (
    <Switch
      value={value}
      onValueChange={onChange}
      disabled={disabled}
      trackColor={{ true: colors.brand, false: colors.outline3 }}
      thumbColor={colors.surface}
      accessibilityElementsHidden
      importantForAccessibility="no"
      {...webThumb}
    />
  );
}
