import { useLocalSearchParams, useRouter } from "expo-router";
import { Check, CircleCheck, Crosshair, MapPin, WifiOff } from "@/components/icons";
import { useEffect, useState } from "react";
import { ActivityIndicator, View } from "react-native";

import { checkIn } from "@/api/ess";
import type { Checkin } from "@/api/types";
import { Button, Card, Screen, Text, ToneIcon } from "@/components";
import { en } from "@/i18n/en";
import { writeErrorMessage } from "@/lib/errors";
import { time } from "@/lib/format";
import { useMutation } from "@/lib/query";
import { useSession } from "@/lib/session";
import { useTheme } from "@/theme/ThemeProvider";
import { radius, space } from "@/theme/tokens";

type Step = "permission" | "locating" | "confirm" | "success";

/** Prototype stand-in for expo-location's permission state; resets on reload. */
let locationGranted = false;

const FIX = { latitude: 10.0159, longitude: 76.3419, accuracy: 12 };

export default function CheckInScreen() {
  const router = useRouter();
  const { colors } = useTheme();
  const { network } = useSession();
  const { type = "IN", step: initialStep } = useLocalSearchParams<{ type?: "IN" | "OUT"; step?: Step }>();
  const [step, setStep] = useState<Step>(initialStep ?? (locationGranted ? "locating" : "permission"));
  const [punch, setPunch] = useState<Checkin | null>(null);
  const [error, setError] = useState<string | null>(null);
  const isIn = type === "IN";

  useEffect(() => {
    if (step !== "locating") return;
    const t = setTimeout(() => setStep("confirm"), 900);
    return () => clearTimeout(t);
  }, [step]);

  const submit = useMutation(checkIn, {
    invalidates: ["ess:today"],
    onSuccess: (p) => {
      setPunch(p);
      setStep("success");
    },
    onError: (e) => setError(writeErrorMessage(e)),
  });

  if (step === "permission") {
    return (
      <Screen
        back
        title={isIn ? en.checkin.title : en.checkin.titleOut}
        footer={
          <View style={{ gap: space.sm }}>
            <Button
              label={en.checkin.allow}
              icon={MapPin}
              variant="primary"
              size="lg"
              full
              onPress={() => {
                locationGranted = true;
                setStep("locating");
              }}
            />
            <Button label={en.checkin.notNow} variant="ghost" full onPress={() => router.back()} />
          </View>
        }
      >
        <View style={{ gap: space.xl, paddingTop: space.xl }}>
          <ToneIcon icon={MapPin} size={64} />
          <View style={{ gap: space.sm }}>
            <Text variant="h1" weight="semibold" accessibilityRole="header">
              {en.checkin.permTitle}
            </Text>
            <Text variant="body" color="ink6">
              {en.checkin.permBody}
            </Text>
          </View>
          <View style={{ gap: space.md }}>
            {en.checkin.permPoints.map((p) => (
              <View key={p} style={{ flexDirection: "row", gap: space.md, alignItems: "flex-start" }}>
                <Check size={18} color={colors.success} style={{ marginTop: 1 }} />
                <Text variant="body" color="ink7" style={{ flex: 1 }}>
                  {p}
                </Text>
              </View>
            ))}
          </View>
        </View>
      </Screen>
    );
  }

  if (step === "success" && punch) {
    return (
      <Screen
        header={<View style={{ height: space.lg }} />}
        footer={<Button label={en.checkin.done} variant="primary" size="lg" full onPress={() => router.replace("/")} />}
      >
        <View style={{ alignItems: "center", gap: space.lg, paddingTop: space.xxxl }} accessibilityLiveRegion="polite">
          <ToneIcon icon={CircleCheck} tone="success" size={80} />
          <Text variant="h1" weight="semibold" style={{ textAlign: "center" }} accessibilityRole="header">
            {isIn ? en.checkin.successIn(time(punch.time)) : en.checkin.successOut(time(punch.time))}
          </Text>
          <Text variant="body" color="ink6" style={{ textAlign: "center" }}>
            {isIn ? en.checkin.successBody : en.checkin.successBodyOut}
          </Text>
        </View>
        <Card>
          <View style={{ flexDirection: "row", gap: space.md, alignItems: "center" }}>
            <MapPin size={18} color={colors.ink5} />
            <Text variant="body" color="ink7" style={{ flex: 1 }}>
              {punch.location}
            </Text>
            <Text variant="body" mono weight="medium">
              {time(punch.time)}
            </Text>
          </View>
        </Card>
      </Screen>
    );
  }

  const offline = network === "offline";
  return (
    <Screen
      back
      title={isIn ? en.checkin.title : en.checkin.titleOut}
      footer={
        <Button
          label={isIn ? en.checkin.confirmIn : en.checkin.confirmOut}
          variant="primary"
          size="lg"
          full
          disabled={step !== "confirm" || offline}
          loading={submit.pending}
          onPress={() => {
            setError(null);
            void submit.mutate(isIn ? "IN" : "OUT", FIX);
          }}
        />
      }
    >
      <View
        style={{
          height: 200,
          borderRadius: radius.lg,
          borderWidth: 1,
          borderColor: colors.outline2,
          backgroundColor: colors.surfaceSunken,
          alignItems: "center",
          justifyContent: "center",
        }}
        accessibilityLabel={step === "confirm" ? `${en.checkin.located}, ${en.checkin.accuracy(FIX.accuracy)}` : en.checkin.locating}
      >
        {step === "locating" ? (
          <View style={{ alignItems: "center", gap: space.md }}>
            <ActivityIndicator color={colors.brand} />
            <Text variant="body" color="ink6">
              {en.checkin.locating}
            </Text>
          </View>
        ) : (
          <View style={{ alignItems: "center" }}>
            {/* Accuracy ring around the pin; a real map tile replaces this in the app build. */}
            <View
              style={{
                width: 96,
                height: 96,
                borderRadius: 48,
                borderWidth: 1.5,
                borderColor: colors.info,
                backgroundColor: colors.infoSoft,
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <MapPin size={28} color={colors.info} fill={colors.infoSoft} />
            </View>
          </View>
        )}
      </View>

      {step === "confirm" ? (
        <Card>
          <View style={{ gap: space.md }}>
            <View style={{ flexDirection: "row", gap: space.md, alignItems: "center" }}>
              <ToneIcon icon={MapPin} size={36} />
              <View style={{ flex: 1 }}>
                <Text variant="title" weight="semibold">
                  {en.checkin.located}
                </Text>
                <Text variant="small" mono color="ink5">
                  {FIX.latitude.toFixed(4)}° N, {FIX.longitude.toFixed(4)}° E
                </Text>
              </View>
            </View>
            <View style={{ flexDirection: "row", gap: space.sm, alignItems: "center" }}>
              <Crosshair size={16} color={colors.ink5} />
              <Text variant="small" color="ink6">
                {en.checkin.accuracy(FIX.accuracy)}
              </Text>
            </View>
            <Text variant="small" color="ink5">
              {en.checkin.serverTime}
            </Text>
          </View>
        </Card>
      ) : null}

      {offline || error ? (
        <View
          accessibilityRole="alert"
          style={{
            flexDirection: "row",
            gap: space.md,
            padding: space.lg,
            borderRadius: radius.lg,
            backgroundColor: colors.dangerSoft,
          }}
        >
          <WifiOff size={18} color={colors.danger} style={{ marginTop: 1 }} />
          <Text variant="body" color="danger" style={{ flex: 1 }}>
            {offline ? en.checkin.offline : error}
          </Text>
        </View>
      ) : null}
    </Screen>
  );
}
