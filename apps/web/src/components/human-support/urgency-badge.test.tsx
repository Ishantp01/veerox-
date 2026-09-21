import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { UrgencyBadge } from "./urgency-badge";

describe("UrgencyBadge", () => {
  it("maps high/urgent/critical to 'High'", () => {
    for (const v of ["high", "urgent", "critical", "HIGH"]) {
      const { unmount } = render(<UrgencyBadge urgency={v} />);
      expect(screen.getByText("High")).toBeInTheDocument();
      unmount();
    }
  });

  it("maps 'low' to 'Low'", () => {
    render(<UrgencyBadge urgency="low" />);
    expect(screen.getByText("Low")).toBeInTheDocument();
  });

  it("defaults unknown/empty/medium to 'Medium'", () => {
    for (const v of ["medium", "", "whatever", null, undefined]) {
      const { unmount } = render(<UrgencyBadge urgency={v} />);
      expect(screen.getByText("Medium")).toBeInTheDocument();
      unmount();
    }
  });
});
