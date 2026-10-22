import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Nav is a single sidebar grouped by function (it replaced the old WhatsApp/Calling tab switcher) and it reads the
// signed-in user to decide which items to show. These tests supply that user directly; the real AuthProvider wraps
// the app in components/providers.tsx.
const { usePathname, authState } = vi.hoisted(() => ({
  usePathname: vi.fn(),
  authState: {
    value: {} as Record<string, unknown>,
  },
}));

vi.mock("next/navigation", () => ({
  usePathname,
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => authState.value,
}));

import Nav from "./nav";

type TestUser = {
  role: "admin" | "member";
  is_superuser: boolean;
  is_platform_org: boolean;
  enabled_features: string[] | null;
};

function signIn(user: Partial<TestUser> = {}) {
  authState.value = {
    isAuthenticated: true,
    logout: vi.fn(),
    user: { role: "admin", is_superuser: false, is_platform_org: false, enabled_features: null, ...user },
  };
}

function renderAt(pathname: string) {
  usePathname.mockReturnValue(pathname);
  return render(<Nav />);
}

const ACTIVE = "border-primary-500";

describe("Nav", () => {
  beforeEach(() => signIn());

  it("shows every functional group to an org admin", () => {
    renderAt("/");
    for (const name of [/^dashboard$/i, /ai calling/i, /ai whatsapp/i, /^leads$/i, /^reports$/i, /voice campaigns/i, /^team$/i, /^settings$/i]) {
      expect(screen.getByRole("link", { name })).toBeInTheDocument();
    }
  });

  it("does not show the platform group to an ordinary org", () => {
    renderAt("/");
    expect(screen.queryByRole("link", { name: /organizations/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /support tickets/i })).not.toBeInTheDocument();
  });

  it("marks Dashboard active only on the exact root path", () => {
    renderAt("/");
    expect(screen.getByRole("link", { name: /^dashboard$/i })).toHaveClass(ACTIVE);
  });

  it("does not mark Dashboard active on a sub-route, but keeps the matching item active", () => {
    renderAt("/crm/leads/123");
    expect(screen.getByRole("link", { name: /^dashboard$/i })).not.toHaveClass(ACTIVE);
    expect(screen.getByRole("link", { name: /^leads$/i })).toHaveClass(ACTIVE);
  });

  it("marks a channel root active only on its own path, not on its nested pages", () => {
    renderAt("/whatsapp/templates");
    expect(screen.getByRole("link", { name: /ai whatsapp/i })).not.toHaveClass(ACTIVE);
    expect(screen.getByRole("link", { name: /whatsapp templates/i })).toHaveClass(ACTIVE);
  });

  it("hides the org back-office pages from a plain member", () => {
    signIn({ role: "member" });
    renderAt("/");
    expect(screen.queryByRole("link", { name: /^team$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /^settings$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /conversation engine/i })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^leads$/i })).toBeInTheDocument();
  });

  it("gives a superuser the platform group and keeps the back-office pages", () => {
    signIn({ role: "member", is_superuser: true });
    renderAt("/");
    expect(screen.getByRole("link", { name: /^organizations$/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /support tickets/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^team$/i })).toBeInTheDocument();
  });

  it("hides a page whose feature the platform has switched off for the org", () => {
    signIn({ enabled_features: ["leads"] });
    renderAt("/");
    expect(screen.getByRole("link", { name: /^leads$/i })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /^appointments$/i })).not.toBeInTheDocument();
  });

  it("offers sign-in when nobody is signed in", () => {
    authState.value = { isAuthenticated: false, logout: vi.fn(), user: null };
    renderAt("/");
    expect(screen.getByRole("link", { name: /sign in|log in|login/i })).toBeInTheDocument();
  });
});
