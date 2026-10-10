import AsyncStorage from "@react-native-async-storage/async-storage";

/**
 * Tiny wrapper so a blocked or cleared store (private browser window, web
 * preview) never breaks the app: reads fall back, writes are best effort.
 */
export async function readPref<T>(key: string, fallback: T): Promise<T> {
  try {
    const raw = await AsyncStorage.getItem(`tbo-smart:${key}`);
    return raw === null ? fallback : (JSON.parse(raw) as T);
  } catch {
    return fallback;
  }
}

export async function writePref<T>(key: string, value: T): Promise<void> {
  try {
    await AsyncStorage.setItem(`tbo-smart:${key}`, JSON.stringify(value));
  } catch {
    // Preference just won't survive a restart.
  }
}
