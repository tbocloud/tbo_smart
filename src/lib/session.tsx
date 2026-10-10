import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { demo, type Connection, type DemoState } from "@/api/client";
import type { Site } from "@/api/types";

import { clearCache, invalidate } from "./query";
import { readPref, writePref } from "./storage";
import { webParam } from "./webParams";

type SessionValue = {
  ready: boolean;
  onboarded: boolean;
  connections: Record<Site, Connection>;
  network: DemoState["network"];
  dataMode: DemoState["data"];
  connect: (site: Site) => void;
  disconnect: (site: Site) => void;
  finishOnboarding: () => void;
  logout: () => void;
  setNetwork: (n: DemoState["network"]) => void;
  setDataMode: (d: DemoState["data"]) => void;
};

const SessionContext = createContext<SessionValue | null>(null);

function connectionParam(site: Site): Connection {
  const v = webParam(site);
  return v === "expired" || v === "disconnected" ? v : "connected";
}

function initialDemo(): Pick<DemoState, "network" | "data"> {
  const mode = webParam("demo");
  const initial: Pick<DemoState, "network" | "data"> = {
    network: mode === "offline" ? "offline" : "online",
    data: mode === "empty" || mode === "error" || mode === "slow" ? mode : "normal",
  };
  demo.network = initial.network;
  demo.data = initial.data;
  return initial;
}

/**
 * Who is connected to which site, plus the prototype's network/data switches.
 * Mirrors into the mock transport (`demo`) so API calls see the same state.
 */
export function SessionProvider({ children }: { children: ReactNode }) {
  // A `?onboarded=` link decides immediately; otherwise wait for the stored flag.
  const [ready, setReady] = useState(() => webParam("onboarded") !== null);
  const [onboarded, setOnboarded] = useState(() => {
    const fromUrl = webParam("onboarded");
    return fromUrl !== null && fromUrl !== "0";
  });
  // Initialisers also seed the transport, because child screens fetch in
  // their effects before this provider's sync effect below runs.
  const [connections, setConnections] = useState<Record<Site, Connection>>(() => {
    const initial = { hub: connectionParam("hub"), ess: connectionParam("ess") };
    demo.connections = initial;
    return initial;
  });
  const [{ network, data }, setDemo] = useState(initialDemo);

  useEffect(() => {
    if (webParam("onboarded") !== null) return;
    void readPref("onboarded", false).then((v) => {
      setOnboarded(v);
      setReady(true);
    });
  }, []);

  // Keep the mock transport in step, then refetch everything.
  useEffect(() => {
    demo.connections = connections;
    demo.network = network;
    demo.data = data;
    invalidate();
  }, [connections, network, data]);

  const setConnection = useCallback((site: Site, value: Connection) => {
    setConnections((c) => ({ ...c, [site]: value }));
  }, []);

  const value = useMemo<SessionValue>(
    () => ({
      ready,
      onboarded,
      connections,
      network,
      dataMode: data,
      connect: (site) => setConnection(site, "connected"),
      disconnect: (site) => setConnection(site, "disconnected"),
      finishOnboarding: () => {
        setOnboarded(true);
        void writePref("onboarded", true);
      },
      logout: () => {
        clearCache();
        setConnections({ hub: "disconnected", ess: "disconnected" });
        setOnboarded(false);
        void writePref("onboarded", false);
      },
      setNetwork: (n) => setDemo((d) => ({ ...d, network: n })),
      setDataMode: (m) => {
        clearCache();
        setDemo((d) => ({ ...d, data: m }));
      },
    }),
    [ready, onboarded, connections, network, data, setConnection],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionValue {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession must be used inside SessionProvider");
  return ctx;
}
