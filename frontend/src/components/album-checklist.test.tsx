import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import AlbumChecklist from "./album-checklist";
import type { Album } from "../types";

const ALBUMS: Album[] = [
  { name: "Été 2024", asset_count: 12 },
  { name: "Vacances", asset_count: 3 },
  { name: "Noël", asset_count: null },
  { name: "Fuji", asset_count: 1394 },
];

function setup(over: Partial<Parameters<typeof AlbumChecklist>[0]> = {}) {
  const onToggle = vi.fn();
  const onOpen = vi.fn();
  render(
    <AlbumChecklist
      albums={ALBUMS}
      selected={new Set(["Vacances"])}
      onToggle={onToggle}
      onOpen={onOpen}
      {...over}
    />,
  );
  return { onToggle, onOpen };
}

afterEach(cleanup);

describe("AlbumChecklist", () => {
  it("renders all albums with counts ('?' when unknown)", () => {
    setup();
    for (const name of ["Été 2024", "Vacances", "Noël", "Fuji"]) {
      expect(screen.getByText(name)).toBeTruthy();
    }
    expect(screen.getByText("1394")).toBeTruthy();
    expect(screen.getByText("?")).toBeTruthy(); // Noël has null count
  });

  it("search filters incrementally, ignoring accents and case", async () => {
    const user = userEvent.setup();
    setup();
    await user.type(screen.getByPlaceholderText("Search albums…"), "ete");
    expect(screen.getByText("Été 2024")).toBeTruthy();
    expect(screen.queryByText("Vacances")).toBeNull();
    expect(screen.queryByText("Fuji")).toBeNull();
  });

  it("search shows the empty state when nothing matches", async () => {
    const user = userEvent.setup();
    setup();
    await user.type(screen.getByPlaceholderText("Search albums…"), "zzz");
    expect(screen.getByText(/No album matches/)).toBeTruthy();
  });

  it("clearing the search restores the full list", async () => {
    const user = userEvent.setup();
    setup();
    const input = screen.getByPlaceholderText("Search albums…");
    await user.type(input, "fuji");
    expect(screen.queryByText("Vacances")).toBeNull();
    await user.clear(input);
    expect(screen.getByText("Vacances")).toBeTruthy();
    expect(screen.getByText("Été 2024")).toBeTruthy();
  });

  it("checkbox state reflects `selected` and toggling calls onToggle", async () => {
    const user = userEvent.setup();
    const { onToggle } = setup();
    const boxes = screen.getAllByRole("checkbox") as HTMLInputElement[];
    // Vacances is pre-selected
    const checked = boxes.filter((b) => b.checked);
    expect(checked).toHaveLength(1);
    await user.click(boxes[0]); // Été 2024
    expect(onToggle).toHaveBeenCalledWith("Été 2024");
  });

  it("toggling works on a filtered list (search + select)", async () => {
    const user = userEvent.setup();
    const { onToggle } = setup();
    await user.type(screen.getByPlaceholderText("Search albums…"), "noel");
    const boxes = screen.getAllByRole("checkbox");
    expect(boxes).toHaveLength(1);
    await user.click(boxes[0]);
    expect(onToggle).toHaveBeenCalledWith("Noël");
  });

  it("clicking the name opens the album (Browser page mode)", async () => {
    const user = userEvent.setup();
    const { onOpen, onToggle } = setup();
    await user.click(screen.getByText("Fuji"));
    expect(onOpen).toHaveBeenCalledWith("Fuji");
    expect(onToggle).not.toHaveBeenCalled(); // opening must not toggle selection
  });
});
