/** URL-backed filter state so screens are shareable/bookmarkable (values are strings). */
import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";

export function useSearchState<K extends string>(keys: readonly K[]) {
  const [params, setParams] = useSearchParams();

  const values = useMemo(() => {
    const out = {} as Record<K, string>;
    for (const k of keys) out[k] = params.get(k) ?? "";
    return out;
  }, [params, keys]);

  // Accepts any string key so it plugs straight into FilterBar's onChange; unknown keys are harmless.
  const set = useCallback(
    (key: string, value: string) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (value === "") next.delete(key);
          else next.set(key, value);
          return next;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  const reset = useCallback(() => {
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        for (const k of keys) next.delete(k);
        return next;
      },
      { replace: true },
    );
  }, [setParams, keys]);

  return { values, set, reset };
}
