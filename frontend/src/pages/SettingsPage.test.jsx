import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import SettingsPage from "./SettingsPage";

vi.mock("../api/endpoints", () => ({
  getMePermissions: vi.fn(),
  listProjects: vi.fn(),
  listAreasScoped: vi.fn(),
  uploadProjectCoverImage: vi.fn(),
  uploadAreaCoverImage: vi.fn(),
  removeProjectCoverImage: vi.fn(),
  removeAreaCoverImage: vi.fn(),
}));

import {
  getMePermissions,
  listAreasScoped,
  listProjects,
  uploadProjectCoverImage,
} from "../api/endpoints";

describe("SettingsPage image management", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    getMePermissions.mockResolvedValue({ role: "admin", project_scope: "ALL" });
    listProjects.mockResolvedValue([{ project_id: "project-uuid", external_id: "P-001", name: "Dự án A", cover_image_url: null }]);
    listAreasScoped.mockResolvedValue([]);
  });

  it("keeps settings unavailable to non-admin principals", async () => {
    getMePermissions.mockResolvedValue({ role: "pipeline_operator", project_scope: "ALL" });
    render(<MemoryRouter><SettingsPage /></MemoryRouter>);
    expect(await screen.findByRole("alert")).toHaveTextContent("không có quyền");
  });

  it("renders read-only project data and uploads through the settings endpoint", async () => {
    uploadProjectCoverImage.mockResolvedValue({ url: "https://res.cloudinary.com/demo/project.jpg", public_id: "project" });
    render(<MemoryRouter><SettingsPage /></MemoryRouter>);

    expect(await screen.findByRole("heading", { name: "Dự án A" })).toBeInTheDocument();
    expect(screen.getByText("Chưa có ảnh bìa")).toBeInTheDocument();

    const file = new File(["image"], "cover.png", { type: "image/png" });
    fireEvent.change(screen.getByLabelText("Chọn ảnh"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "Tải ảnh" }));

    await waitFor(() => expect(uploadProjectCoverImage).toHaveBeenCalledWith("project-uuid", file));
    expect(await screen.findByRole("status")).toHaveTextContent("Đã cập nhật ảnh bìa dự án");
  });
});
