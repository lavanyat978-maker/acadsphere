import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorMessage } from "./api";

interface ApiState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
  setData: (value: T | null) => void;
}

export function useApi<T>(path: string | null, options?: { pollMs?: number }): ApiState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const mounted = useRef(true);
  const loadedOnce = useRef(false);

  const reload = useCallback(() => setTick((value) => value + 1), []);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    if (!path) {
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    let cancelled = false;
    if (!loadedOnce.current) setLoading(true);
    api<T>(path, { signal: controller.signal })
      .then((result) => {
        if (cancelled) return;
        setData(result);
        setError(null);
        loadedOnce.current = true;
      })
      .catch((err: unknown) => {
        if (cancelled || (err instanceof DOMException && err.name === "AbortError")) return;
        setError(errorMessage(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [path, tick]);

  useEffect(() => {
    if (!options?.pollMs || !path) return;
    const timer = window.setInterval(() => setTick((value) => value + 1), options.pollMs);
    return () => window.clearInterval(timer);
  }, [options?.pollMs, path]);

  return { data, error, loading, reload, setData };
}
