import { useRouter, type Href } from "expo-router";
import { BellOff, CheckCheck, Headset, UsersRound } from "@/components/icons";
import { useState } from "react";
import { View } from "react-native";

import { getNotifications as getEssNotifications, markNotificationsRead as markEssRead } from "@/api/ess";
import { getNotifications as getHubNotifications, markNotificationsRead as markHubRead } from "@/api/hub";
import type { AppNotification, Site } from "@/api/types";
import { Button, Chips, EmptyState, Group, Pressable, QueryView, Screen, Text, ToneIcon, useToast } from "@/components";
import { en } from "@/i18n/en";
import { writeErrorMessage } from "@/lib/errors";
import { byNewest, relativeStamp } from "@/lib/format";
import { useMutation, useQuery } from "@/lib/query";
import { useTheme } from "@/theme/ThemeProvider";
import { space } from "@/theme/tokens";

type Filter = "all" | Site;

export default function Alerts() {
  const toast = useToast();
  const [filter, setFilter] = useState<Filter>("all");
  const hub = useQuery("hub:notifications", getHubNotifications);
  const ess = useQuery("ess:notifications", getEssNotifications);

  // One site failing (e.g. HR session ended) must not hide the other site's alerts.
  const merged = [...(hub.data ?? []), ...(ess.data ?? [])].sort(byNewest);
  const list = filter === "all" ? merged : merged.filter((n) => n.site === filter);
  const unread = merged.filter((n) => !n.read).length;
  const missing = [hub.error && !hub.data ? en.sites.hubLabel : null, ess.error && !ess.data ? en.sites.essLabel : null].filter(
    Boolean,
  ) as string[];

  const markAll = useMutation(
    async () => {
      if (hub.data) await markHubRead("all");
      if (ess.data) await markEssRead("all");
    },
    {
      invalidates: ["hub:notifications", "ess:notifications", "hub:home"],
      onError: (e) => toast(writeErrorMessage(e), "danger"),
    },
  );

  // Both sites failed: fall back to the shared error/reconnect state.
  const combined = {
    ...hub,
    data: hub.data || ess.data ? list : undefined,
    loading: hub.loading && ess.loading,
    error: hub.error && ess.error ? hub.error : null,
    retry: () => {
      hub.retry();
      ess.retry();
    },
  };

  return (
    <Screen
      title={en.alerts.title}
      subtitle={unread ? en.alerts.unread(unread) : en.alerts.allRead}
      right={
        unread ? (
          <Button
            label={en.alerts.markAll}
            icon={CheckCheck}
            variant="ghost"
            loading={markAll.pending}
            onPress={() => void markAll.mutate()}
          />
        ) : null
      }
      onRefresh={() => {
        hub.refresh();
        ess.refresh();
      }}
      refreshing={hub.refreshing}
    >
      <Chips<Filter>
        label={en.alerts.title}
        value={filter}
        onChange={setFilter}
        options={[
          { value: "all", label: en.alerts.filters.all },
          { value: "hub", label: en.alerts.filters.hub },
          { value: "ess", label: en.alerts.filters.ess },
        ]}
      />
      {missing.map((site) => (
        <Text key={site} variant="small" color="ink5">
          {en.alerts.siteMissing(site)}
        </Text>
      ))}
      <QueryView query={combined}>
        {(items) =>
          items.length === 0 ? (
            <EmptyState icon={BellOff} title={en.alerts.empty} body={en.alerts.emptyBody} />
          ) : (
            <Group>
              {items.map((n, i) => (
                <AlertRow key={n.name} item={n} last={i === items.length - 1} />
              ))}
            </Group>
          )
        }
      </QueryView>
    </Screen>
  );
}

function AlertRow({ item, last }: { item: AppNotification; last: boolean }) {
  const router = useRouter();
  const { colors } = useTheme();
  const read = useMutation(item.site === "hub" ? markHubRead : markEssRead, {
    invalidates: [`${item.site}:notifications`, "hub:home"],
  });
  const siteLabel = item.site === "hub" ? en.sites.hubLabel : en.sites.essLabel;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`${item.read ? "" : `${en.a11y.unread}. `}${siteLabel}. ${item.title}. ${item.body}. ${relativeStamp(item.at)}`}
      onPress={() => {
        if (!item.read) void read.mutate([item.name]);
        router.push(item.route as Href);
      }}
      ringRadius={0}
      style={({ pressed }) => ({
        flexDirection: "row",
        gap: space.md,
        padding: space.lg,
        backgroundColor: pressed ? colors.surfaceSunken : colors.surface,
        borderBottomWidth: last ? 0 : 1,
        borderBottomColor: colors.outline1,
      })}
    >
      <ToneIcon icon={item.site === "hub" ? Headset : UsersRound} size={36} />
      <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
        <View style={{ flexDirection: "row", alignItems: "center", gap: space.sm }}>
          <Text variant="caption" weight="medium" color="ink5" style={{ flex: 1 }}>
            {siteLabel.toUpperCase()}
          </Text>
          <Text variant="caption" mono color="ink5">
            {relativeStamp(item.at)}
          </Text>
        </View>
        <Text variant="body" weight={item.read ? "regular" : "semibold"}>
          {item.title}
        </Text>
        <Text variant="small" color="ink6" numberOfLines={2}>
          {item.body}
        </Text>
      </View>
      {!item.read ? (
        <View
          accessibilityElementsHidden
          style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: colors.brand, marginTop: 6 }}
        />
      ) : null}
    </Pressable>
  );
}
