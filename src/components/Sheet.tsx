import { X } from "@/components/icons";
import type { ReactNode } from "react";
import { KeyboardAvoidingView, Modal, Platform, Pressable as RNPressable, ScrollView, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { en } from "@/i18n/en";
import { useTheme } from "@/theme/ThemeProvider";
import { radius, space } from "@/theme/tokens";

import { IconButton } from "./IconButton";
import { Text } from "./Text";

type Props = {
  open: boolean;
  onClose: () => void;
  title: string;
  subtitle?: string;
  children: ReactNode;
  /** Sticky action area (the sheet's one primary action). */
  footer?: ReactNode;
};

export function Sheet({ open, onClose, title, subtitle, children, footer }: Props) {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  return (
    <Modal visible={open} transparent animationType="fade" onRequestClose={onClose} statusBarTranslucent>
      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
        <View style={{ flex: 1, justifyContent: "flex-end", backgroundColor: colors.scrim }}>
          <RNPressable
            style={{ position: "absolute", inset: 0 }}
            onPress={onClose}
            accessibilityRole="button"
            accessibilityLabel={en.a11y.close}
          />
          <View
            accessibilityViewIsModal
            style={{
              backgroundColor: colors.surface,
              borderTopLeftRadius: radius.xl,
              borderTopRightRadius: radius.xl,
              maxHeight: "88%",
              paddingBottom: Math.max(insets.bottom, space.lg),
              width: "100%",
              maxWidth: 560,
              alignSelf: "center",
            }}
          >
            <View style={{ alignItems: "center", paddingTop: space.sm }}>
              <View style={{ width: 36, height: 4, borderRadius: 2, backgroundColor: colors.outline3 }} />
            </View>
            <View
              style={{
                flexDirection: "row",
                alignItems: "center",
                paddingLeft: space.lg,
                paddingRight: space.xs,
                paddingTop: space.xs,
              }}
            >
              <View style={{ flex: 1, minWidth: 0 }}>
                <Text variant="title" weight="semibold" accessibilityRole="header">
                  {title}
                </Text>
                {subtitle ? (
                  <Text variant="small" color="ink5" numberOfLines={2}>
                    {subtitle}
                  </Text>
                ) : null}
              </View>
              <IconButton icon={X} label={en.a11y.close} onPress={onClose} />
            </View>
            <ScrollView
              contentContainerStyle={{ padding: space.lg, gap: space.lg }}
              keyboardShouldPersistTaps="handled"
            >
              {children}
            </ScrollView>
            {footer ? <View style={{ paddingHorizontal: space.lg, paddingTop: space.sm }}>{footer}</View> : null}
          </View>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}
