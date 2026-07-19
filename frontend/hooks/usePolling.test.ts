import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { usePolling } from "./usePolling";

describe("usePolling", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("calls the callback repeatedly at the given interval", () => {
    const callback = vi.fn();
    renderHook(() => usePolling(callback, 1000));

    expect(callback).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1000);
    expect(callback).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(2000);
    expect(callback).toHaveBeenCalledTimes(3);
  });

  it("does not poll when enabled is false", () => {
    const callback = vi.fn();
    renderHook(() => usePolling(callback, 1000, false));

    vi.advanceTimersByTime(5000);
    expect(callback).not.toHaveBeenCalled();
  });

  it("always calls the latest callback, not a stale closure", () => {
    let renderedCallback = vi.fn();
    const { rerender } = renderHook(
      ({ cb }) => usePolling(cb, 1000),
      { initialProps: { cb: renderedCallback } }
    );

    const newCallback = vi.fn();
    rerender({ cb: newCallback });

    vi.advanceTimersByTime(1000);
    expect(newCallback).toHaveBeenCalledTimes(1);
    expect(renderedCallback).not.toHaveBeenCalled();
  });
});
