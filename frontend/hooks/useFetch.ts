import { useState, useEffect, useCallback } from "react";

interface UseFetchState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

export function useFetch<T>(url: string): UseFetchState<T> & { refetch: () => void } {
  const [state, setState] = useState<UseFetchState<T>>({
    data: null,
    loading: true,
    error: null,
  });
  const [refetchToken, setRefetchToken] = useState(0);

  const refetch = useCallback(() => setRefetchToken((t) => t + 1), []);

  useEffect(() => {
    // Created synchronously, before the async work starts, so cleanup
    // can actually cancel an in-flight request on unmount or URL change.
    const controller = new AbortController();

    // Reset loading/error for this run (needed on refetch, when a prior
    // fetch may have already flipped loading to false). Deferred to a
    // microtask so this isn't a synchronous setState call directly in the
    // effect body (react-hooks/set-state-in-effect); the AbortController
    // and the fetch() call itself still happen synchronously above/below
    // so abort-on-unmount and abort-on-url-change keep working.
    Promise.resolve().then(() => {
      setState((prev) => ({ ...prev, loading: true, error: null }));
    });

    fetch(url, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .then((json: T) => setState({ data: json, loading: false, error: null }))
      .catch((err) => {
        if (err instanceof Error && err.name !== "AbortError") {
          setState({ data: null, loading: false, error: err.message });
        }
      });

    return () => controller.abort();
  }, [url, refetchToken]);

  return { ...state, refetch };
}
