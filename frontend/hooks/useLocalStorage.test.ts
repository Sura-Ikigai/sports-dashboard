import { describe, it, expect, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useLocalStorage } from "./useLocalStorage";

describe("useLocalStorage", () => {
  beforeEach(() => window.localStorage.clear());

  it("initializes from an existing localStorage value", () => {
    window.localStorage.setItem("count", JSON.stringify(5));
    const { result } = renderHook(() => useLocalStorage("count", 0));
    expect(result.current[0]).toBe(5);
  });

  it("falls back to the initial value when nothing is stored", () => {
    const { result } = renderHook(() => useLocalStorage("missing-key", 42));
    expect(result.current[0]).toBe(42);
  });

  it("persists updates to localStorage", () => {
    const { result } = renderHook(() => useLocalStorage("count", 0));
    act(() => result.current[1](10));
    expect(JSON.parse(window.localStorage.getItem("count")!)).toBe(10);
  });
});
