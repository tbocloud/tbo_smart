import { ChevronDown, type LucideIcon } from "@/components/icons";
import { View } from "react-native";

import { useTheme } from "@/theme/ThemeProvider";
import { radius, space, TAP } from "@/theme/tokens";

import { Pressable } from "./Pressable";
import { Text } from "./Text";

type Props = {
  label: string;
  value: string | null;
  placeholder: string;
  onPress: () => void;
  icon?: LucideIcon;
  error?: string;
  help?: string;
};

/** Looks like an input, opens a picker sheet (select, date, person). */
export function FieldButton({ label, value, placeholder, onPress, icon: Icon = ChevronDown, error, help }: Props) {
  const { colors } = useTheme();
  return (
    <View style={{ gap: space.xs, flex: 1, minWidth: 0 }}>
      <Text variant="small" weight="medium" color="ink7">
        {label}
      </Text>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`${label}: ${value ?? placeholder}`}
        accessibilityHint={error ?? help}
        onPress={onPress}
        ringRadius={radius.md}
        style={({ pressed }) => ({
          minHeight: TAP,
          borderRadius: radius.md,
          borderWidth: error ? 1.5 : 1,
          borderColor: error ? colors.danger : colors.outline3,
          backgroundColor: pressed ? colors.surfaceSunken : colors.surface,
          paddingHorizontal: space.md,
          flexDirection: "row",
          alignItems: "center",
          gap: space.sm,
        })}
      >
        <Text variant="body" color={value ? "ink9" : "ink4"} style={{ flex: 1 }} numberOfLines={1}>
          {value ?? placeholder}
        </Text>
        <Icon size={18} color={colors.ink5} />
      </Pressable>
      {error ? (
        <Text variant="small" color="danger" accessibilityRole="alert">
          {error}
        </Text>
      ) : help ? (
        <Text variant="small" color="ink5">
          {help}
        </Text>
      ) : null}
    </View>
  );
}
