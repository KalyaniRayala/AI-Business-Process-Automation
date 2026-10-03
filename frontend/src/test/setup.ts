import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

import { assertNoUnmockedCalls } from "./mockApi";

// jsdom has no layout, so scrolling is a stub that tests can inspect.
window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();

  // A test that called an API route it did not mock is a broken test.
  assertNoUnmockedCalls();
});
