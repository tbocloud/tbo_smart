import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import { ApiError } from "@/api/client";

/**
 * Minimal read cache: keeps the last good data per key so screens show it
 * while refreshing or offline (brief §6), and lets writes invalidate reads.
 * Small on purpose; swap for TanStack Query if the app outgrows it.
 */
const cache = new Map<string, unknown>();
const listeners = new Set<(prefix: string) => void>();

export function invalidate(prefix = ""): void {
  for (const listener of listeners) listener(prefix);
}

type QueryState<T> = {
  data: T | undefined;
  error: ApiError | null;
  loading: boolean;
  refreshing: boolean;
};

export type Query<T> = QueryState<T> & {
  refresh: () => void;
  retry: () => void;
};

function toApiError(e: unknown): ApiError {
  return e instanceof ApiError ? e : new ApiError("server", "hub");
}

function initialState<T>(key: string): QueryState<T> & { key: string } {
  return { key, data: cache.get(key) as T | undefined, error: null, loading: !cache.has(key), refreshing: false };
}

export function useQuery<T>(key: string, fetcher: () => Promise<T>): Query<T> {
  const fetcherRef = useRef(fetcher);
  useLayoutEffect(() => {
    fetcherRef.current = fetcher;
  });
  const [state, setState] = useState(() => initialState<T>(key));
  // A new key (e.g. another ticket) starts from that key's cache, not the old data.
  if (state.key !== key) setState(initialState<T>(key));
  const run = useRef(0);

  // Only sets state after the await, so effects that call it don't cascade renders.
  const fetchNow = useCallback(async () => {
    const id = ++run.current;
    try {
      const data = await fetcherRef.current();
      cache.set(key, data);
      if (id === run.current) setState({ key, data, error: null, loading: false, refreshing: false });
    } catch (e) {
      if (id === run.current) {
        setState({ key, data: cache.get(key) as T | undefined, error: toApiError(e), loading: false, refreshing: false });
      }
    }
  }, [key]);

  useEffect(() => {
    void fetchNow();
  }, [fetchNow]);

  useEffect(() => {
    const listener = (prefix: string) => {
      if (key.startsWith(prefix)) void fetchNow();
    };
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, [key, fetchNow]);

  return {
    data: state.data,
    error: state.error,
    loading: state.loading,
    refreshing: state.refreshing,
    refresh: () => {
      setState((s) => ({ ...s, refreshing: true }));
      void fetchNow();
    },
    retry: () => {
      setState((s) => ({ ...s, error: null, loading: !cache.has(key) }));
      void fetchNow();
    },
  };
}

/** Clears every cached read (logout, or switching prototype data modes). */
export function clearCache(): void {
  cache.clear();
}

/**
 * Runs a write once at a time (no double submits), reports the error, and
 * invalidates the given read keys on success.
 */
export function useMutation<A extends unknown[], R>(
  action: (...args: A) => Promise<R>,
  options: { invalidates?: string[]; onSuccess?: (result: R) => void; onError?: (e: ApiError) => void } = {},
) {
  const [pending, setPending] = useState(false);
  const busy = useRef(false);
  const optionsRef = useRef(options);
  useLayoutEffect(() => {
    optionsRef.current = options;
  });

  const mutate = useCallback(
    async (...args: A) => {
      if (busy.current) return;
      busy.current = true;
      setPending(true);
      try {
        const result = await action(...args);
        for (const k of optionsRef.current.invalidates ?? []) invalidate(k);
        optionsRef.current.onSuccess?.(result);
      } catch (e) {
        optionsRef.current.onError?.(toApiError(e));
      } finally {
        busy.current = false;
        setPending(false);
      }
    },
    [action],
  );

  return { mutate, pending };
}
