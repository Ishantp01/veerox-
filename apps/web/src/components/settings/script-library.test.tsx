import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// The backend refuses script writes from a plain org member (403). The library mirrors that: a member can still read
// the list, but every write control (add, edit, delete, set default) is hidden.
vi.mock("@/lib/hooks", () => {
  const mutation = () => ({ mutate: vi.fn(), isPending: false });
  const scripts = [
    { id: "s1", name: "Main script", content: "Greet the caller", channel: "voice", is_default: true, phone_number_id: null },
    { id: "s2", name: "Backup script", content: "Short pitch", channel: "voice", is_default: false, phone_number_id: null },
  ];
  return {
    useScripts: () => ({ data: scripts, isLoading: false, isError: false, error: null, refetch: vi.fn() }),
    useOrgNumbers: () => ({ data: { phone_numbers: [] } }),
    useCreateScript: mutation,
    useUpdateScriptLibraryItem: mutation,
    useSetDefaultScript: mutation,
    useDeleteScript: mutation,
  };
});

vi.mock("@/components/ui", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/components/ui")>()),
  useConfirm: () => vi.fn(),
  useToast: () => ({ toast: vi.fn() }),
}));

import { ScriptLibrary } from "./script-library";

describe("ScriptLibrary", () => {
  it("shows every write control to an org admin", () => {
    render(<ScriptLibrary channel="voice" canEdit />);
    expect(screen.getByText("Main script")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /new script|add script/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /delete main script/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /set default/i })).toBeInTheDocument();
  });

  it("defaults to editable, so other callers are unchanged", () => {
    render(<ScriptLibrary channel="voice" />);
    expect(screen.getByRole("button", { name: /delete main script/i })).toBeInTheDocument();
  });

  it("lets a member read the scripts but hides add, edit, delete and set-default", () => {
    render(<ScriptLibrary channel="voice" canEdit={false} />);
    expect(screen.getByText("Main script")).toBeInTheDocument();
    expect(screen.getByText("Backup script")).toBeInTheDocument();
    expect(screen.getByText("Greet the caller")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /new script|add script/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /delete/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /set default/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /edit/i })).not.toBeInTheDocument();
    expect(screen.getAllByText("View only")).toHaveLength(2);
    expect(screen.getByText(/only an org admin can add or change scripts/i)).toBeInTheDocument();
  });
});
