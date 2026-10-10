import Constants from "expo-constants";
import { useRouter } from "expo-router";
import { CircleCheck, Headset, LogOut, Trophy, TriangleAlert, Unplug, UsersRound, type LucideIcon } from "@/components/icons";
import { useState } from "react";
import { View } from "react-native";

import type { Connection } from "@/api/client";
import { getScoreboard } from "@/api/hub";
import type { Site } from "@/api/types";
import {
  Badge,
  Button,
  Chips,
  Group,
  ListRow,
  Screen,
  SectionHeader,
  SegmentedControl,
  SwitchRow,
  Text,
  ToneIcon,
} from "@/components";
import { en } from "@/i18n/en";
import { useQuery } from "@/lib/query";
import { useSession } from "@/lib/session";
import { useTheme, type ThemePreference } from "@/theme/ThemeProvider";
import { space } from "@/theme/tokens";

export default function More() {
  const router = useRouter();
  const { preference, setPreference } = useTheme();
  const session = useSession();
  const score = useQuery("hub:scoreboard", getScoreboard);
  const [prefs, setPrefs] = useState({ tickets: true, tasks: true, hr: true, files: false });

  return (
    <Screen title={en.more.title}>
      <Group>
        <ListRow
          title={en.more.scoreboard}
          subtitle={score.data ? en.more.scoreboardHint(score.data.me.rank, score.data.me.of) : undefined}
          leading={<ToneIcon icon={Trophy} size={36} />}
          onPress={() => router.push("/scoreboard")}
          divider={false}
        />
      </Group>

      <View style={{ gap: space.md }}>
        <SectionHeader title={en.more.accounts} />
        <Group>
          <AccountRow site="hub" icon={Headset} title={en.sites.hub} host="teams.teambackoffice.com" state={session.connections.hub} />
          <AccountRow site="ess" icon={UsersRound} title={en.sites.ess} host="tboindia.tbocloud.in" state={session.connections.ess} last />
        </Group>
      </View>

      <View style={{ gap: space.md }}>
        <SectionHeader title={en.more.notifications} />
        <Group>
          <SwitchRow label={en.more.notifyTickets} value={prefs.tickets} onChange={(v) => setPrefs({ ...prefs, tickets: v })} divider />
          <SwitchRow label={en.more.notifyTasks} value={prefs.tasks} onChange={(v) => setPrefs({ ...prefs, tasks: v })} divider />
          <SwitchRow label={en.more.notifyHr} value={prefs.hr} onChange={(v) => setPrefs({ ...prefs, hr: v })} divider />
          <SwitchRow label={en.more.notifyFiles} value={prefs.files} onChange={(v) => setPrefs({ ...prefs, files: v })} />
        </Group>
      </View>

      <View style={{ gap: space.md }}>
        <SectionHeader title={en.more.appearance} />
        <SegmentedControl<ThemePreference>
          label={en.more.appearance}
          value={preference}
          onChange={setPreference}
          options={[
            { value: "system", label: en.more.theme.system },
            { value: "light", label: en.more.theme.light },
            { value: "dark", label: en.more.theme.dark },
          ]}
        />
      </View>

      <View style={{ gap: space.md }}>
        <SectionHeader title={en.more.prototype} />
        <Text variant="small" color="ink5">
          {en.more.prototypeHint}
        </Text>
        <Chips
          label={en.a11y.network}
          value={session.network}
          onChange={session.setNetwork}
          options={[
            { value: "online", label: en.more.network.online },
            { value: "offline", label: en.more.network.offline },
          ]}
        />
        <Chips
          label={en.a11y.dataMode}
          value={session.dataMode}
          onChange={session.setDataMode}
          options={[
            { value: "normal", label: en.more.data.normal },
            { value: "slow", label: en.more.data.slow },
            { value: "empty", label: en.more.data.empty },
            { value: "error", label: en.more.data.error },
          ]}
        />
      </View>

      <Group>
        <ListRow
          title={en.more.version}
          divider={false}
          trailing={
            <Text variant="body" mono color="ink6">
              {Constants.expoConfig?.version ?? "0.1.0"}
            </Text>
          }
        />
      </Group>

      <View style={{ gap: space.sm }}>
        <Button
          label={en.more.logout}
          icon={LogOut}
          variant="danger"
          full
          onPress={() => {
            session.logout();
            router.replace("/onboarding");
          }}
        />
        <Text variant="small" color="ink5" style={{ textAlign: "center" }}>
          {en.more.logoutHint}
        </Text>
      </View>
    </Screen>
  );
}

function AccountRow({
  site,
  icon,
  title,
  host,
  state,
  last,
}: {
  site: Site;
  icon: LucideIcon;
  title: string;
  host: string;
  state: Connection;
  last?: boolean;
}) {
  const { colors } = useTheme();
  const { connect, disconnect } = useSession();
  const badge =
    state === "connected" ? (
      <Badge label={en.onboarding.connected} tone="success" icon={CircleCheck} />
    ) : state === "expired" ? (
      <Badge label={en.more.expired} tone="warning" icon={TriangleAlert} />
    ) : (
      <Badge label={en.more.disconnected} icon={Unplug} />
    );
  return (
    <View
      style={{
        padding: space.lg,
        gap: space.md,
        borderBottomWidth: last ? 0 : 1,
        borderBottomColor: colors.outline1,
      }}
    >
      <View style={{ flexDirection: "row", gap: space.md, alignItems: "flex-start" }}>
        <ToneIcon icon={icon} size={36} />
        <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
          <View style={{ flexDirection: "row", alignItems: "center", gap: space.sm }}>
            <Text variant="body" weight="medium" style={{ flex: 1 }}>
              {title}
            </Text>
            {badge}
          </View>
          <Text variant="small" mono color="ink5" numberOfLines={1}>
            {state === "connected" ? en.more.connectedAs : host}
          </Text>
        </View>
      </View>
      {state === "connected" ? (
        <Button
          label={en.more.disconnect}
          variant="secondary"
          onPress={() => disconnect(site)}
          style={{ alignSelf: "flex-start", marginLeft: 36 + space.md }}
        />
      ) : (
        <Button
          label={`${en.common.reconnect} ${title}`}
          onPress={() => connect(site)}
          style={{ alignSelf: "flex-start", marginLeft: 36 + space.md }}
        />
      )}
    </View>
  );
}
