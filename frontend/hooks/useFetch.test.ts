import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { useFetch } from "./useFetch";

describe("useFetch", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("returns data on a successful fetch", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ hello: "world" }),
    }) as unknown as typeof fetch;

    const { result } = renderHook(() => useFetch<{ hello: string }>("/api/test"));

    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toEqual({ hello: "world" });
    expect(result.current.error).toBeNull();
  });

  it("sets an error message on a non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({}),
    }) as unknown as typeof fetch;

    const { result } = renderHook(() => useFetch<unknown>("/api/test"));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("HTTP 500");
    expect(result.current.data).toBeNull();
  });

  it("aborts the in-flight request when the component unmounts", async () => {
    let capturedSignal: AbortSignal | undefined;
    global.fetch = vi.fn((_url: RequestInfo | URL, opts?: RequestInit) => {
      capturedSignal = opts?.signal ?? undefined;
      return new Promise(() => {}); // never resolves -- simulates an in-flight request
    }) as unknown as typeof fetch;

    const { unmount } = renderHook(() => useFetch<unknown>("/api/test"));
    unmount();

    expect(capturedSignal?.aborted).toBe(true);
  });

  it("refetch() triggers a new request", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ count: 1 }),
    }) as unknown as typeof fetch;

    const { result } = renderHook(() => useFetch<{ count: number }>("/api/test"));
    await waitFor(() => expect(result.current.loading).toBe(false));

    result.current.refetch();

    await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(2));
  });
});
