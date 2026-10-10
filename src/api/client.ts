import type { Site } from "./types";

/**
 * Mock transport. Every API function goes through `call`, so swapping in the
 * real HTTP clients later (bearer token, refresh on 401, Frappe error mapping)
 * happens here and nowhere else.
 */

export type ApiErrorCode = "offline" | "server" | "reconnect" | "forbidden";

export class ApiError extends Error {
  constructor(
    public code: ApiErrorCode,
    public site: Site,
  ) {
    super(code);
  }
}

export type Connection = "connected" | "disconnected" | "expired";

/** Prototype controls (More → Prototype). Lets the owner see every state. */
export type DemoState = {
  network: "online" | "offline";
  data: "normal" | "slow" | "empty" | "error";
  connections: Record<Site, Connection>;
  /** "Preview as" in More: staff sees their own work, manager adds the Team hub. */
  role: "staff" | "manager";
};

export const demo: DemoState = {
  network: "online",
  data: "normal",
  connections: { hub: "connected", ess: "connected" },
  role: "staff",
};

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Reads. Offline, the real app serves its on-device cache of the last sync,
 * so the mock keeps returning data and the offline banner explains it.
 */
export async function call<T>(site: Site, produce: (empty: boolean) => T): Promise<T> {
  await wait(demo.data === "slow" ? 2200 : 350);
  if (demo.connections[site] !== "connected") throw new ApiError("reconnect", site);
  if (demo.data === "error") throw new ApiError("server", site);
  return produce(demo.data === "empty");
}

/** Writes never fake success while offline (brief §4.3: "don't fake a check-in"). */
export async function write<T>(site: Site, produce: () => T): Promise<T> {
  await wait(demo.data === "slow" ? 2200 : 500);
  if (demo.network === "offline") throw new ApiError("offline", site);
  if (demo.connections[site] !== "connected") throw new ApiError("reconnect", site);
  if (demo.data === "error") throw new ApiError("server", site);
  return produce();
}
