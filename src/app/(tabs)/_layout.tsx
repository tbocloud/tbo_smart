import { Redirect, Tabs } from "expo-router";
import { Bell, BriefcaseBusiness, CircleUserRound, House, Menu } from "@/components/icons";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { useUnreadCount } from "@/features/alerts/useUnreadCount";
import { en } from "@/i18n/en";
import { useSession } from "@/lib/session";
import { useTheme } from "@/theme/ThemeProvider";
import { fonts } from "@/theme/tokens";

export default function TabLayout() {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const { ready, onboarded } = useSession();
  const unread = useUnreadCount();
  if (!ready) return null;
  if (!onboarded) return <Redirect href="/onboarding" />;

  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: colors.brand,
        tabBarInactiveTintColor: colors.ink5,
        tabBarStyle: {
          backgroundColor: colors.surface,
          borderTopColor: colors.outline2,
          height: 68 + insets.bottom,
          paddingTop: 4,
          paddingBottom: insets.bottom + 4,
        },
        tabBarLabelStyle: { fontFamily: fonts.medium, fontSize: 12, lineHeight: 16 },
        tabBarBadgeStyle: { backgroundColor: colors.brand, color: colors.onBrand, fontFamily: fonts.monoMedium, fontSize: 10 },
        sceneStyle: { backgroundColor: colors.bg },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{ title: en.tabs.home, tabBarIcon: ({ color }) => <House size={22} color={color} strokeWidth={1.75} /> }}
      />
      <Tabs.Screen
        name="work"
        options={{
          title: en.tabs.work,
          tabBarIcon: ({ color }) => <BriefcaseBusiness size={22} color={color} strokeWidth={1.75} />,
        }}
      />
      <Tabs.Screen
        name="hr"
        options={{ title: en.tabs.hr, tabBarIcon: ({ color }) => <CircleUserRound size={22} color={color} strokeWidth={1.75} /> }}
      />
      <Tabs.Screen
        name="alerts"
        options={{
          title: en.tabs.alerts,
          tabBarBadge: unread || undefined,
          tabBarAccessibilityLabel: unread ? `${en.tabs.alerts}, ${unread} unread` : en.tabs.alerts,
          tabBarIcon: ({ color }) => <Bell size={22} color={color} strokeWidth={1.75} />,
        }}
      />
      <Tabs.Screen
        name="more"
        options={{ title: en.tabs.more, tabBarIcon: ({ color }) => <Menu size={22} color={color} strokeWidth={1.75} /> }}
      />
    </Tabs>
  );
}
