import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAlbums } from "./use-albums";

const mocks = vi.hoisted(() => ({
  albums: vi.fn(),
  albumCount: vi.fn(),
}));

vi.mock("../api", () => ({ api: mocks }));

beforeEach(() => {
  mocks.albums.mockReset();
  mocks.albumCount.mockReset();
});

describe("useAlbums", () => {
  it("returns the list immediately, then fills counts lazily", async () => {
    mocks.albums.mockResolvedValue([
      { name: "Fuji", asset_count: null },
      { name: "Vacances", asset_count: null },
    ]);
    mocks.albumCount.mockImplementation(async (name: string) => ({
      name,
      asset_count: name === "Fuji" ? 1394 : 3,
    }));

    const { result } = renderHook(() => useAlbums());
    await waitFor(() => expect(result.current.albums).toHaveLength(2));
    await waitFor(() =>
      expect(result.current.albums.map((a) => a.asset_count)).toEqual([1394, 3]),
    );
    expect(mocks.albumCount).toHaveBeenCalledTimes(2);
  });

  it("a failing count leaves that album at '?' without killing the list", async () => {
    mocks.albums.mockResolvedValue([
      { name: "Ok", asset_count: null },
      { name: "Boom", asset_count: null },
    ]);
    mocks.albumCount.mockImplementation(async (name: string) => {
      if (name === "Boom") throw new Error("count failed");
      return { name, asset_count: 7 };
    });

    const { result } = renderHook(() => useAlbums());
    await waitFor(() =>
      expect(result.current.albums.find((a) => a.name === "Ok")?.asset_count).toBe(7),
    );
    expect(result.current.albums.find((a) => a.name === "Boom")?.asset_count).toBeNull();
    expect(result.current.error).toBeNull();
  });

  it("skips count fetches when the backend already sent counts", async () => {
    mocks.albums.mockResolvedValue([{ name: "Full", asset_count: 42 }]);
    const { result } = renderHook(() => useAlbums());
    await waitFor(() => expect(result.current.albums).toHaveLength(1));
    expect(mocks.albumCount).not.toHaveBeenCalled();
  });

  it("surfaces a list-level error", async () => {
    mocks.albums.mockRejectedValue(new Error("offline"));
    const { result } = renderHook(() => useAlbums());
    await waitFor(() => expect(result.current.error).toBe("offline"));
  });
});
