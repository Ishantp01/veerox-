import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

const { usePathname, push } = vi.hoisted(() => ({
  usePathname: vi.fn(),
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  usePathname,
  useRouter: () => ({ push }),
}));

import Nav from "./nav";

function renderAt(pathname: string) {
  usePathname.mockReturnValue(pathname);
  push.mockClear();
  return render(<Nav />);
}

describe("Nav", () => {
  it("shows only the WhatsApp section's items when on a /whatsapp route", () => {
    renderAt("/whatsapp/conversations");
    expect(screen.getByRole("link", { name: /send message/i })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /dial/i })).not.toBeInTheDocument();
  });

  it("shows only the Calling section's items when on a /calling route", () => {
    renderAt("/calling/leads");
    expect(screen.getByRole("link", { name: /dial/i })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /send message/i })).not.toBeInTheDocument();
  });

  it("marks the Dashboard link active only on the section's exact root path", () => {
    renderAt("/whatsapp");
    expect(screen.getByRole("link", { name: /dashboard/i })).toHaveClass("bg-indigo-600");
  });

  it("does not mark Dashboard active on a sub-route", () => {
    renderAt("/whatsapp/conversations");
    expect(screen.getByRole("link", { name: /dashboard/i })).not.toHaveClass("bg-indigo-600");
    expect(screen.getByRole("link", { name: /conversations/i })).toHaveClass("bg-indigo-600");
  });

  it("defaults to the WhatsApp section on neutral routes like the landing page", () => {
    renderAt("/");
    expect(screen.getByRole("link", { name: /send message/i })).toBeInTheDocument();
  });

  it("switches section when the inactive switcher tab is clicked", async () => {
    const user = userEvent.setup();
    renderAt("/whatsapp");
    await user.click(screen.getByRole("tab", { name: /calling/i }));
    expect(push).toHaveBeenCalledWith("/calling");
  });
});
