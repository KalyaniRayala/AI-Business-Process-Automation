import {
  Activity,
  ClipboardCheck,
  Compass,
  LayoutDashboard,
  Lightbulb,
  Menu,
  Monitor,
  Moon,
  Network,
  ScrollText,
  Sparkles,
  Sun,
  Workflow,
  X,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import { api } from "../api/client";
import { useTheme, type ThemeChoice } from "../hooks/useTheme";

const HEALTH_INTERVAL_MS = 30_000;

interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  end?: boolean;
}

const GROUPS: { label: string; links: NavItem[] }[] = [
  {
    label: "Workspace",
    links: [
      { to: "/", label: "Overview", icon: LayoutDashboard, end: true },
      { to: "/workflows", label: "Workflows", icon: Workflow },
      { to: "/approvals", label: "Approvals", icon: ClipboardCheck },
    ],
  },
  {
    label: "Insights",
    links: [
      { to: "/discovery", label: "Discovery", icon: Compass },
      { to: "/recommendations", label: "Recommendations", icon: Lightbulb },
    ],
  },
  {
    label: "Governance",
    links: [
      { to: "/audit", label: "Audit trail", icon: ScrollText },
      { to: "/context", label: "Context graph", icon: Network },
    ],
  },
  {
    label: "Platform",
    links: [{ to: "/system", label: "System", icon: Activity }],
  },
];

type Health = "checking" | "online" | "offline";

function useBackendHealth(): Health {
  const [health, setHealth] = useState<Health>("checking");

  useEffect(() => {
    let active = true;
    let controller = new AbortController();

    async function check() {
      controller.abort();
      controller = new AbortController();

      try {
        const result = await api.health(controller.signal);
        if (active) setHealth(result.status === "ok" ? "online" : "offline");
      } catch (failure) {
        if (active && !(failure instanceof DOMException)) setHealth("offline");
      }
    }

    void check();
    const timer = window.setInterval(check, HEALTH_INTERVAL_MS);

    return () => {
      active = false;
      controller.abort();
      window.clearInterval(timer);
    };
  }, []);

  return health;
}

/** Number of workflows waiting for a decision (refreshed on navigation). */
function usePendingApprovals(): number | null {
  const [pending, setPending] = useState<number | null>(null);
  const { pathname } = useLocation();

  useEffect(() => {
    const controller = new AbortController();

    api
      .listWorkflows({ status: "awaiting_approval", limit: 1 }, controller.signal)
      .then((page) => setPending(page.total))
      .catch(() => {
        if (!controller.signal.aborted) setPending(null);
      });

    return () => controller.abort();
  }, [pathname]);

  return pending;
}

/**
 * A client-side route change keeps the scroll position of the page before it,
 * so opening a page from the bottom of a long one would land you mid-way down
 * the next. Start every page at the top. Only the path counts: a filter that
 * changes the query string should not throw you back up the page.
 */
function useScrollToTopOnNavigation() {
  const { pathname } = useLocation();
  const first = useRef(true);

  useEffect(() => {
    // The browser already put the first page where it belongs, including
    // after a reload.
    if (first.current) {
      first.current = false;
      return;
    }

    window.scrollTo({ top: 0, left: 0, behavior: "instant" });
  }, [pathname]);
}

const HEALTH_LABELS: Record<Health, string> = {
  checking: "Checking backend...",
  online: "Backend online",
  offline: "Backend offline",
};

const THEME_LABELS: Record<ThemeChoice, string> = {
  system: "System",
  light: "Light",
  dark: "Dark",
};

const THEME_ICONS: Record<ThemeChoice, LucideIcon> = {
  system: Monitor,
  light: Sun,
  dark: Moon,
};

function Brand() {
  return (
    <div className="brand">
      <span className="brand__mark" aria-hidden="true">
        <Sparkles />
      </span>
      <span className="brand__name">
        <strong>Business Process</strong>
        <span>Automation</span>
      </span>
    </div>
  );
}

export function Layout() {
  const health = useBackendHealth();
  const pending = usePendingApprovals();
  const theme = useTheme();
  const { pathname } = useLocation();

  const [navOpen, setNavOpen] = useState(false);
  const menuButton = useRef<HTMLButtonElement>(null);
  const sidebar = useRef<HTMLElement>(null);

  useScrollToTopOnNavigation();

  // Choosing a page closes the drawer on a narrow screen.
  useEffect(() => {
    setNavOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (!navOpen) return undefined;

    // Move focus into the drawer so a keyboard user lands on its links.
    sidebar.current?.querySelector("a")?.focus();

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setNavOpen(false);
        menuButton.current?.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown);

    return () => document.removeEventListener("keydown", onKeyDown);
  }, [navOpen]);

  const ThemeIcon = THEME_ICONS[theme.choice];

  return (
    <div className={`shell${navOpen ? " shell--nav-open" : ""}`}>
      <header className="topbar">
        <button
          ref={menuButton}
          type="button"
          className="menu-button"
          aria-label={navOpen ? "Close navigation" : "Open navigation"}
          aria-expanded={navOpen}
          aria-controls="app-sidebar"
          onClick={() => setNavOpen((open) => !open)}
        >
          {navOpen ? <X aria-hidden="true" /> : <Menu aria-hidden="true" />}
        </button>

        <Brand />
      </header>

      {navOpen && <div className="scrim" aria-hidden="true" onClick={() => setNavOpen(false)} />}

      <aside id="app-sidebar" ref={sidebar} className="sidebar">
        <Brand />

        <nav className="nav" aria-label="Main">
          {GROUPS.map((group, index) => (
            <div key={group.label}>
              <p className="nav-group__label" id={`nav-group-${index}`}>
                {group.label}
              </p>

              <ul aria-labelledby={`nav-group-${index}`}>
                {group.links.map((link) => (
                  <li key={link.to}>
                    <NavLink to={link.to} end={link.end}>
                      <link.icon aria-hidden="true" />
                      {link.label}
                      {link.to === "/approvals" && pending ? (
                        <span className="nav-count" aria-label={`${pending} waiting`}>
                          {pending}
                        </span>
                      ) : null}
                    </NavLink>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>

        <div className="sidebar__footer">
          <p className={`health health--${health}`} role="status">
            <span className="health__dot" aria-hidden="true" />
            {HEALTH_LABELS[health]}
          </p>

          <button
            type="button"
            className="theme-toggle"
            title="Switch theme"
            onClick={theme.cycle}
          >
            <ThemeIcon aria-hidden="true" />
            Theme: {THEME_LABELS[theme.choice]}
          </button>
        </div>
      </aside>

      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
