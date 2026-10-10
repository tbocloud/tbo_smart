import { useState } from "react";
import { TextInput, View, type TextInputProps } from "react-native";

import { useTheme } from "@/theme/ThemeProvider";
import { fonts, radius, space, TAP, type } from "@/theme/tokens";

import { Text } from "./Text";

type Props = Omit<TextInputProps, "style"> & {
  label: string;
  help?: string;
  error?: string;
  optional?: boolean;
  mono?: boolean;
};

/** Visible label above, help below, errors announced (guidelines §10). */
export function TextField({ label, help, error, optional, mono, multiline, ...rest }: Props) {
  const { colors } = useTheme();
  const [focused, setFocused] = useState(false);
  return (
    <View style={{ gap: space.xs }}>
      <Text variant="small" weight="medium" color="ink7">
        {label}
        {optional ? <Text variant="small" color="ink5">{"  ·  Optional"}</Text> : null}
      </Text>
      <TextInput
        {...rest}
        multiline={multiline}
        accessibilityLabel={label}
        accessibilityHint={error ?? help}
        placeholderTextColor={colors.ink4}
        onFocus={(e) => {
          setFocused(true);
          rest.onFocus?.(e);
        }}
        onBlur={(e) => {
          setFocused(false);
          rest.onBlur?.(e);
        }}
        style={{
          ...type.body,
          fontFamily: mono ? fonts.mono : fonts.regular,
          color: colors.ink9,
          minHeight: multiline ? 96 : TAP,
          paddingHorizontal: space.md,
          paddingVertical: multiline ? space.md : 0,
          textAlignVertical: multiline ? "top" : "center",
          borderRadius: radius.md,
          borderWidth: focused || error ? 1.5 : 1,
          borderColor: error ? colors.danger : focused ? colors.brand : colors.outline3,
          backgroundColor: colors.surface,
          outlineStyle: "none" as never,
        }}
      />
      {error ? (
        <Text variant="small" color="danger" accessibilityLiveRegion="polite" accessibilityRole="alert">
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
