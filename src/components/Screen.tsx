import { useRouter } from "expo-router";
import { ArrowLeft } from "@/components/icons";
import type { ReactNode } from "react";
import { RefreshControl, ScrollView, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { en } from "@/i18n/en";
import { useTheme } from "@/theme/ThemeProvider";
import { space } from "@/theme/tokens";

import { IconButton } from "./IconButton";
import { OfflineBanner } from "./OfflineBanner";
import { Text } from "./Text";

type Props = {
  title?: string;
  subtitle?: string;
  /** Show a back button (stack screens). Tab roots use a large title instead. */
  back?: boolean;
  right?: ReactNode;
  children: ReactNode;
  onRefresh?: () => void;
  refreshing?: boolean;
  scroll?: boolean;
  /** Pinned below the content, above the home indicator (composers, form actions). */
  footer?: ReactNode;
  /** Replaces the default header entirely (Home's greeting). */
  header?: ReactNode;
};

export function Screen({ title, subtitle, back, right, children, onRefresh, refreshing, scroll = true, footer, header }: Props) {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();

  const headerView = header ?? (
    <View
      style={{
        flexDirection: "row",
        alignItems: "center",
        minHeight: 56,
        paddingLeft: back ? space.xs : space.lg,
        paddingRight: space.xs,
        gap: back ? 0 : space.sm,
      }}
    >
      {back ? (
        <IconButton
          icon={ArrowLeft}
          label={en.common.back}
          onPress={() => (router.canGoBack() ? router.back() : router.replace("/"))}
        />
      ) : null}
      <View style={{ flex: 1, minWidth: 0, paddingRight: space.sm }}>
        {title ? (
          <Text variant={back ? "title" : "h1"} weight="semibold" numberOfLines={1} accessibilityRole="header">
            {title}
          </Text>
        ) : null}
        {subtitle ? (
          <Text variant="small" color="ink5" numberOfLines={1}>
            {subtitle}
          </Text>
        ) : null}
      </View>
      {right}
    </View>
  );

  const body = { padding: space.lg, gap: space.lg, paddingBottom: footer ? space.lg : space.xxxl };

  return (
    <View style={{ flex: 1, backgroundColor: colors.bg, paddingTop: insets.top }}>
      <View style={{ backgroundColor: colors.bg }}>{headerView}</View>
      <OfflineBanner />
      {scroll ? (
        <ScrollView
          style={{ flex: 1 }}
          contentContainerStyle={body}
          keyboardShouldPersistTaps="handled"
          refreshControl={
            onRefresh ? (
              <RefreshControl
                refreshing={!!refreshing}
                onRefresh={onRefresh}
                tintColor={colors.brand}
                colors={[colors.brand]}
              />
            ) : undefined
          }
        >
          {children}
        </ScrollView>
      ) : (
        <View style={{ flex: 1 }}>{children}</View>
      )}
      {footer ? (
        <View
          style={{
            paddingHorizontal: space.lg,
            paddingTop: space.md,
            paddingBottom: Math.max(insets.bottom, space.md),
            backgroundColor: colors.surface,
            borderTopWidth: 1,
            borderTopColor: colors.outline2,
          }}
        >
          {footer}
        </View>
      ) : null}
    </View>
  );
}
