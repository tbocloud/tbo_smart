import { CircleAlert, CircleCheck, type LucideIcon } from "@/components/icons";
import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";
import { View } from "react-native";
import Animated, { FadeInDown, FadeOutDown } from "react-native-reanimated";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { useTheme } from "@/theme/ThemeProvider";
import { radius, space } from "@/theme/tokens";

import { Text } from "./Text";

type ToastTone = "success" | "danger" | "neutral";
type ToastItem = { id: number; message: string; tone: ToastTone };

const ToastContext = createContext<(message: string, tone?: ToastTone) => void>(() => undefined);

export function ToastProvider({ children }: { children: ReactNode }) {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const [toast, setToast] = useState<ToastItem | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const show = useCallback((message: string, tone: ToastTone = "neutral") => {
    if (timer.current) clearTimeout(timer.current);
    setToast({ id: Date.now(), message, tone });
    timer.current = setTimeout(() => setToast(null), tone === "danger" ? 5000 : 2800);
  }, []);

  const icons: Record<ToastTone, LucideIcon | null> = { success: CircleCheck, danger: CircleAlert, neutral: null };
  const Icon = toast ? icons[toast.tone] : null;

  return (
    <ToastContext.Provider value={show}>
      {children}
      <View
        pointerEvents="none"
        style={{ position: "absolute", left: 0, right: 0, bottom: insets.bottom + 72, alignItems: "center" }}
      >
        {toast ? (
          <Animated.View
            key={toast.id}
            entering={FadeInDown.duration(180)}
            exiting={FadeOutDown.duration(160)}
            accessibilityLiveRegion="polite"
            accessibilityRole="alert"
            style={{
              flexDirection: "row",
              alignItems: "center",
              gap: space.sm,
              maxWidth: 400,
              marginHorizontal: space.lg,
              paddingVertical: space.md,
              paddingHorizontal: space.lg,
              borderRadius: radius.lg,
              backgroundColor: colors.ink9,
            }}
          >
            {Icon ? <Icon size={20} color={colors.inkInverse} /> : null}
            <Text variant="body" color="inkInverse" style={{ flexShrink: 1 }}>
              {toast.message}
            </Text>
          </Animated.View>
        ) : null}
      </View>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext);
}
