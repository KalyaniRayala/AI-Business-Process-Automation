import { useCallback, useEffect, useState } from "react";

export type ThemeChoice = "system" | "light" | "dark";

const STORAGE_KEY = "bpa-theme";

/** The order the toggle cycles through. */
export const THEME_ORDER: readonly ThemeChoice[] = ["system", "light", "dark"];

export function nextTheme(current: ThemeChoice): ThemeChoice {
  const index = THEME_ORDER.indexOf(current);

  return THEME_ORDER[(index + 1) % THEME_ORDER.length] ?? "system";
}

function isChoice(value: unknown): value is ThemeChoice {
  return value === "system" || value === "light" || value === "dark";
}

/**
 * Storage can be missing or throw (private windows, blocked site data), so the
 * theme must still work without it: it simply is not remembered.
 */
function readStored(): ThemeChoice {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);

    return isChoice(value) ? value : "system";
  } catch {
    return "system";
  }
}

function writeStored(choice: ThemeChoice) {
  try {
    if (choice === "system") {
      window.localStorage.removeItem(STORAGE_KEY);
    } else {
      window.localStorage.setItem(STORAGE_KEY, choice);
    }
  } catch {
    // Not remembered; the choice still applies for this visit.
  }
}

/** "system" removes the attribute so the stylesheet follows the OS setting. */
export function applyTheme(choice: ThemeChoice) {
  const root = document.documentElement;

  if (choice === "system") {
    delete root.dataset["theme"];
  } else {
    root.dataset["theme"] = choice;
  }
}

export function useTheme() {
  const [choice, setChoice] = useState<ThemeChoice>(readStored);

  // Applying and remembering are side effects, so they live here and not
  // inside the state updater below, which React may run more than once.
  useEffect(() => {
    applyTheme(choice);
    writeStored(choice);
  }, [choice]);

  const cycle = useCallback(() => {
    setChoice((current) => nextTheme(current));
  }, []);

  return { choice, cycle };
}
