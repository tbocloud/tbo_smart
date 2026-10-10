import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useColorScheme } from "react-native";

import { readPref, writePref } from "@/lib/storage";
import { webParam } from "@/lib/webParams";

import { dark, light, type ColorTokens } from "./tokens";

export type ThemePreference = "system" | "light" | "dark";

type ThemeValue = {
  colors: ColorTokens;
  scheme: "light" | "dark";
  preference: ThemePreference;
  setPreference: (p: ThemePreference) => void;
};

const ThemeContext = createContext<ThemeValue | null>(null);

function initialPreference(): ThemePreference {
  const fromUrl = webParam("theme");
  return fromUrl === "light" || fromUrl === "dark" ? fromUrl : "system";
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const system = useColorScheme();
  const [preference, setPreferenceState] = useState<ThemePreference>(initialPreference);

  useEffect(() => {
    if (webParam("theme")) return;
    void readPref<ThemePreference>("theme", "system").then(setPreferenceState);
  }, []);

  const value = useMemo<ThemeValue>(() => {
    const scheme = preference === "system" ? (system === "dark" ? "dark" : "light") : preference;
    return {
      colors: scheme === "dark" ? dark : light,
      scheme,
      preference,
      setPreference: (p) => {
        setPreferenceState(p);
        void writePref("theme", p);
      },
    };
  }, [preference, system]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used inside ThemeProvider");
  return ctx;
}
