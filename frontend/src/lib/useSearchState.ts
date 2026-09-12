/**
 * URL-backed filter state so screens are shareable/bookmarkable (values are strings).
 *
 * Consecutive `set` calls in the same tick are merged: react-router's functional
 * `setSearchParams` reads the params from its render closure, so without merging the
 * last call would silently discard the earlier ones (e.g. set("sort") + set("page")).
 */
import { useCallback, useEffect, useMemo, useRef } from "react";
import { useSearchParams } from "react-router-dom";

export function useSearchState<K extends string>(keys: readonly K[]) {
  const [params, setParams] = useSearchParams();
  const pending = useRef<URLSearchParams | null>(null);

  // Once the router has applied a navigation the pending batch is stale.
  useEffect(() => {
    pending.current = null;
  }, [params]);

  const values = useMemo(() => {
    const out = {} as Record<K, string>;
    for (const k of keys) out[k] = params.get(k) ?? "";
    return out;
  }, [params, keys]);

  const apply = useCallback(
    (mutate: (next: URLSearchParams) => void) => {
      const next = new URLSearchParams(pending.current ?? params);
      mutate(next);
      pending.current = next;
      setParams(next, { replace: true });
    },
    [params, setParams],
  );

  // Accepts any string key so it plugs straight into FilterBar's onChange; unknown keys are harmless.
  const set = useCallback(
    (key: string, value: string) => {
      apply((next) => {
        if (value === "") next.delete(key);
        else next.set(key, value);
      });
    },
    [apply],
  );

  /** Sets several keys at once (empty string deletes). */
  const setMany = useCallback(
    (patch: Record<string, string>) => {
      apply((next) => {
        for (const [key, value] of Object.entries(patch)) {
          if (value === "") next.delete(key);
          else next.set(key, value);
        }
      });
    },
    [apply],
  );

  const reset = useCallback(() => {
    apply((next) => {
      for (const k of keys) next.delete(k);
    });
  }, [apply, keys]);

  return { values, set, setMany, reset };
}
