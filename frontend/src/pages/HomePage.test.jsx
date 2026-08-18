import React from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import HomePage from "./HomePage";
import { HOMEPAGE_CAROUSEL_IMAGES } from "./homepageCarouselData";

const breakpointState = vi.hoisted(() => ({ isNarrow: false, isMobile: false }));

vi.mock("../hooks/useBreakpoint", () => ({
  useBreakpoint: () => breakpointState,
}));

function renderPage({ isNarrow = false, isMobile = false } = {}) {
  Object.assign(breakpointState, { isNarrow, isMobile });
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/import" element={<div>IMPORT_ROUTE</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("HomePage", () => {
  it("renders the public intelligence story and accessible landmarks", () => {
    renderPage();

    expect(screen.getByRole("banner")).toBeInTheDocument();
    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(screen.getByText("NẠP DỮ LIỆU VÀO MỘT GÓC NHÌN")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Tập trung dữ liệu bán hàng.");
    expect(screen.getByText("AbsorpIQ tập trung dữ liệu dự án, tồn kho và giao dịch để theo dõi chỉ báo hấp thụ. Đồng bộ CRM hiện chỉ dành cho Mini CRM đã cấu hình.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Khám phá luồng dữ liệu/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Xem dữ liệu mẫu" })).toBeInTheDocument();
    expect(screen.getByText("Dữ liệu theo phạm vi")).toBeInTheDocument();
    expect(screen.getByText("Theo dõi hấp thụ theo dự án")).toBeInTheDocument();
    expect(screen.getByText("Dữ liệu minh họa cho bản demo. Không hiển thị dữ liệu dự án thực tế.")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Hình nền giới thiệu AbsorpIQ" })).toBeInTheDocument();
    expect(screen.queryByText("Dự báo nhu cầu")).not.toBeInTheDocument();
    expect(screen.queryByText("Xưởng kịch bản")).not.toBeInTheDocument();
  });

  it("supports the chart view toggles", () => {
    renderPage();

    const illustrativeToggle = screen.getByRole("button", { name: "Tham chiếu minh họa" });
    fireEvent.click(illustrativeToggle);
    expect(illustrativeToggle).toHaveAttribute("aria-pressed", "true");
  });

  it("renders the CRM integration flow without fabricated metrics or charts", async () => {
    renderPage();

    const card = screen.getByRole("article", { name: "Tích hợp CRM" });
    expect(within(card).getByText("Kết nối dữ liệu bán hàng để theo dõi dự án chính xác hơn")).toBeInTheDocument();
    expect(within(card).getByText("Đồng bộ dự án, phân khu, căn hộ và giao dịch từ Mini CRM đã cấu hình.", { exact: false })).toBeInTheDocument();
    expect(within(card).getByRole("list", { name: "Luồng dữ liệu CRM" })).toBeInTheDocument();
    expect(within(card).getByText("Kết nối CRM để bắt đầu xem số liệu thực tế.")).toBeInTheDocument();
    expect(within(card).getByText("Đồng bộ dữ liệu từ CRM theo chu kỳ")).toBeInTheDocument();
    expect(within(card).queryByText("412")).not.toBeInTheDocument();
    expect(within(card).queryByText("68,4%")).not.toBeInTheDocument();
    expect(card.querySelector(".miniChart")).toBeNull();
    expect(card.querySelector(".miniBar")).toBeNull();
    expect(within(card).queryByText(/thời gian thực/i)).not.toBeInTheDocument();

    fireEvent.click(within(card).getByRole("button", { name: /^Kết nối CRM/ }));
    expect(await screen.findByText("IMPORT_ROUTE")).toBeInTheDocument();
  });

  it("keeps the CRM introduction compact on mobile", () => {
    renderPage({ isNarrow: true, isMobile: true });

    const card = screen.getByRole("article", { name: "Tích hợp CRM" });
    expect(card.parentElement).toHaveStyle({ width: "100%" });
    expect(within(card).getByRole("button", { name: /^Kết nối CRM/ })).toBeInTheDocument();
    expect(within(card).getByRole("list", { name: "Luồng dữ liệu CRM" })).toBeInTheDocument();
  });

  it("provides accessible manual carousel controls", () => {
    renderPage();

    const carousel = screen.getByRole("region", { name: "Hình nền giới thiệu AbsorpIQ" });
    expect(within(carousel).getByRole("button", { name: "Ảnh trước" })).toBeInTheDocument();
    expect(within(carousel).getByRole("button", { name: "Ảnh tiếp theo" })).toBeInTheDocument();
    expect(carousel.querySelectorAll(".background-carousel-dots button")).toHaveLength(3);

    fireEvent.click(within(carousel).getByRole("button", { name: "Chuyển đến ảnh 2" }));
    expect(within(carousel).getByRole("button", { name: "Chuyển đến ảnh 2" })).toHaveAttribute("aria-current", "true");
  });

  it("uses three Vite-resolved local image assets", () => {
    expect(HOMEPAGE_CAROUSEL_IMAGES).toHaveLength(3);
    expect(new Set(HOMEPAGE_CAROUSEL_IMAGES).size).toBe(3);
    expect(HOMEPAGE_CAROUSEL_IMAGES.every((src) => src.includes("/assets/images/"))).toBe(true);
    expect(HOMEPAGE_CAROUSEL_IMAGES.every((src) => !src.includes("frontend/src/"))).toBe(true);
  });

  it("keeps carousel photos clear and limits contrast treatment to the copy edge", () => {
    renderPage();

    const heroOverlay = document.querySelector("#overview > div[style]");
    const insightOverlay = document.querySelector("#insights > div[style]");
    expect(heroOverlay.style.width).toBe("42vw");
    expect(heroOverlay.style.maxWidth).toBe("42vw");
    expect(insightOverlay.style.width).toBe("42vw");
    expect(insightOverlay.style.maxWidth).toBe("42vw");

    const css = document.querySelector(".home-shell > style").textContent;
    expect(css).toContain(".background-carousel-overlay { background: transparent");
    expect(css).not.toContain("rgba(7,17,31,.98)");
    expect(css).not.toContain("filter: brightness");
    expect(css).not.toContain("mix-blend-mode");
  });
});
