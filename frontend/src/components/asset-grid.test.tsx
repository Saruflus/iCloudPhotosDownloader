import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import AssetGrid from "./asset-grid";
import type { Asset } from "../types";

const mk = (i: number, over: Partial<Asset> = {}): Asset => ({
  asset_id: `a${i}`,
  filename: `IMG_${i}.HEIC`,
  media_type: "HEIC",
  media_category: "HEIC",
  file_size: 1,
  created_at: null,
  is_live_photo: false,
  has_edited_version: false,
  has_raw_version: false,
  thumbnail_url: `/api/assets/a${i}/thumbnail`,
  ...over,
});

afterEach(cleanup);

describe("AssetGrid (virtualized)", () => {
  it("renders only the visible window of a huge list", () => {
    const assets = Array.from({ length: 2000 }, (_, i) => mk(i));
    render(<AssetGrid assets={assets} selected={new Set()} onToggle={() => {}} />);
    const imgs = document.querySelectorAll("img");
    expect(imgs.length).toBeGreaterThan(0);
    // 2000 tiles would hang the DOM; virtualization must cap what's mounted.
    expect(imgs.length).toBeLessThan(200);
  });

  it("clicking a tile toggles that asset", async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn();
    render(<AssetGrid assets={[mk(1), mk(2)]} selected={new Set()} onToggle={onToggle} />);
    await user.click(document.querySelectorAll("button")[0]);
    expect(onToggle).toHaveBeenCalledWith("a1");
  });

  it("shows badges on tiles (RAW)", () => {
    render(
      <AssetGrid
        assets={[mk(1, { media_type: "RAF", media_category: "RAW" })]}
        selected={new Set()}
        onToggle={() => {}}
      />,
    );
    expect(screen.getByText("RAW")).toBeTruthy();
  });

  it("calls onEndReached when the last rows render (short list = all visible)", () => {
    const onEnd = vi.fn();
    render(
      <AssetGrid assets={[mk(1), mk(2), mk(3)]} selected={new Set()} onToggle={() => {}} onEndReached={onEnd} />,
    );
    expect(onEnd).toHaveBeenCalled();
  });
});
