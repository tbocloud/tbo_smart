import { useRouter } from "expo-router";
import { CircleCheck, Headset, ShieldCheck, UsersRound, type LucideIcon } from "@/components/icons";
import { useState } from "react";
import { ScrollView, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import type { Site } from "@/api/types";
import { Button, Card, Text, ToneIcon } from "@/components";
import { en } from "@/i18n/en";
import { useSession } from "@/lib/session";
import { useTheme } from "@/theme/ThemeProvider";
import { radius, space } from "@/theme/tokens";

/**
 * Two OAuth (PKCE) sign-ins, one per site. The real flow opens the system
 * browser; the prototype simulates the round trip.
 */
export default function Onboarding() {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const { connections, connect, disconnect, finishOnboarding } = useSession();
  const [busy, setBusy] = useState<Site | null>(null);
  // Fresh install: both start disconnected until the user connects each.
  const [done, setDone] = useState<Record<Site, boolean>>({ hub: false, ess: false });

  const run = (site: Site) => {
    setBusy(site);
    setTimeout(() => {
      connect(site);
      setDone((d) => ({ ...d, [site]: true }));
      setBusy(null);
    }, 900);
  };

  const finish = () => {
    if (!done.ess) disconnect("ess");
    finishOnboarding();
    router.replace("/");
  };

  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: colors.bg }}
      contentContainerStyle={{
        paddingTop: insets.top + space.xxl,
        paddingBottom: insets.bottom + space.xl,
        paddingHorizontal: space.xl,
        gap: space.xl,
        flexGrow: 1,
      }}
    >
      <View style={{ gap: space.md }}>
        <View
          style={{
            width: 48,
            height: 48,
            borderRadius: radius.lg,
            backgroundColor: colors.brand,
            alignItems: "center",
            justifyContent: "center",
          }}
          accessibilityLabel={en.a11y.logo}
          accessibilityRole="image"
        >
          <Text variant="title" weight="semibold" color="onBrand">
            TBO
          </Text>
        </View>
        <Text variant="h1" weight="semibold" accessibilityRole="header">
          {en.onboarding.title}
        </Text>
        <Text variant="body" color="ink6">
          {en.onboarding.body}
        </Text>
      </View>

      <View style={{ gap: space.md }}>
        <ConnectStep
          step={1}
          icon={Headset}
          title={en.onboarding.hubTitle}
          body={en.onboarding.hubBody}
          done={done.hub && connections.hub === "connected"}
          busy={busy === "hub"}
          active={!done.hub}
          onConnect={() => run("hub")}
        />
        <ConnectStep
          step={2}
          icon={UsersRound}
          title={en.onboarding.essTitle}
          body={en.onboarding.essBody}
          done={done.ess && connections.ess === "connected"}
          busy={busy === "ess"}
          active={done.hub && !done.ess}
          onConnect={() => run("ess")}
        />
      </View>

      <View style={{ flex: 1 }} />

      <View style={{ gap: space.md }}>
        <View style={{ flexDirection: "row", gap: space.sm, alignItems: "flex-start" }}>
          <ShieldCheck size={16} color={colors.ink5} style={{ marginTop: 2 }} />
          <Text variant="small" color="ink5" style={{ flex: 1 }}>
            {en.onboarding.privacy}
          </Text>
        </View>
        {/* One primary action at a time: Connect while a step is open, Continue once both are done. */}
        {done.hub && done.ess ? (
          <Button label={en.onboarding.continue} variant="primary" size="lg" full onPress={finish} />
        ) : null}
        {done.hub && !done.ess ? <Button label={en.onboarding.skipHr} variant="ghost" full onPress={finish} /> : null}
      </View>
    </ScrollView>
  );
}

function ConnectStep(props: {
  step: number;
  icon: LucideIcon;
  title: string;
  body: string;
  done: boolean;
  busy: boolean;
  active: boolean;
  onConnect: () => void;
}) {
  const { colors } = useTheme();
  return (
    <Card style={props.active ? { borderColor: colors.outline3 } : undefined}>
      <View style={{ flexDirection: "row", gap: space.md }}>
        <ToneIcon icon={props.done ? CircleCheck : props.icon} tone={props.done ? "success" : "neutral"} size={40} />
        <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
          <Text variant="caption" color="ink5">
            {en.onboarding.step(props.step)}
          </Text>
          <Text variant="title" weight="semibold">
            {props.title}
          </Text>
          <Text variant="small" color="ink6">
            {props.body}
          </Text>
          {props.done ? (
            <Text variant="small" color="success" weight="medium" style={{ marginTop: space.xs }}>
              {en.onboarding.connected} <Text variant="small" mono color="ink5">{en.onboarding.asEmail}</Text>
            </Text>
          ) : null}
        </View>
      </View>
      {!props.done ? (
        <Button
          label={props.busy ? en.onboarding.connecting : en.onboarding.connect}
          variant={props.active ? "primary" : "secondary"}
          disabled={!props.active}
          loading={props.busy}
          full
          onPress={props.onConnect}
          style={{ marginTop: space.lg }}
        />
      ) : null}
    </Card>
  );
}
