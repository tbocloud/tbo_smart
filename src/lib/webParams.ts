import { Platform } from "react-native";

/**
 * Prototype-only: on web, `?theme=dark`, `?demo=offline` etc. preset the app
 * so screenshots and review links can open straight into a state. Native
 * builds ignore it.
 */
export function webParam(name: string): string | null {
  if (Platform.OS !== "web" || typeof window === "undefined") return null;
  try {
    return new URLSearchParams(window.location.search).get(name);
  } catch {
    return null;
  }
}
