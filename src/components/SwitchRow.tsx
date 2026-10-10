import { View } from "react-native";

import { useTheme } from "@/theme/ThemeProvider";
import { space } from "@/theme/tokens";

import { Pressable } from "./Pressable";
import { Text } from "./Text";
import { Toggle } from "./Toggle";

type Props = {
  label: string;
  help?: string;
  value: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
  divider?: boolean;
};

/** Whole row toggles, so the target is the full width, not just the switch. */
export function SwitchRow({ label, help, value, onChange, disabled, divider }: Props) {
  const { colors } = useTheme();
  return (
    <Pressable
      accessibilityRole="switch"
      accessibilityLabel={label}
      accessibilityHint={help}
      accessibilityState={{ checked: value, disabled }}
      disabled={disabled}
      onPress={() => onChange(!value)}
      ringRadius={0}
      style={{
        flexDirection: "row",
        alignItems: "center",
        gap: space.md,
        minHeight: 56,
        paddingVertical: space.sm,
        paddingHorizontal: space.lg,
        opacity: disabled ? 0.5 : 1,
        borderBottomWidth: divider ? 1 : 0,
        borderBottomColor: colors.outline1,
      }}
    >
      <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
        <Text variant="body" weight="medium">
          {label}
        </Text>
        {help ? (
          <Text variant="small" color="ink5">
            {help}
          </Text>
        ) : null}
      </View>
      <Toggle value={value} onChange={onChange} disabled={disabled} />
    </Pressable>
  );
}
