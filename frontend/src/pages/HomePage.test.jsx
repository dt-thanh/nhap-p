import React from "react";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
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
      </Routes>
    </MemoryRouter>,
  );
}

describe("HomePage", () => {
  it("renders the public intelligence story and accessible landmarks", () => {
    renderPage();

    const hero = document.querySelector("#overview");
    expect(screen.getByRole("banner")).toBeInTheDocument();
    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Biết căn nào cần ưu tiên");
    expect(screen.getByText("AbsorpIQ tổng hợp dữ liệu dự án, tồn kho và giao dịch để theo dõi nhịp độ hấp thụ, xếp hạng ưu tiên theo căn và hỗ trợ phân tích bằng AI. Đội ngũ kiểm tra dữ liệu trước khi hành động.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Khám phá dữ liệu bán hàng/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Xem dữ liệu mẫu" })).toBeInTheDocument();
    expect(within(hero).queryByText(/dự báo|ngay lập tức/i)).not.toBeInTheDocument();
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

  it("renders the calm CRM-to-analytics data pipeline in resources", () => {
    renderPage();

    const resources = document.querySelector("#resources");
    expect(within(resources).getByRole("heading", { name: "Từ CRM đến góc nhìn sẵn sàng hành động" })).toBeInTheDocument();
    expect(within(resources).getByRole("group", { name: "Luồng dữ liệu qua sáu giai đoạn từ đồng bộ đến tư vấn" })).toBeInTheDocument();
    const stageLegend = within(resources).getByRole("list", { name: "Các giai đoạn quy trình dữ liệu" });
    expect(stageLegend).toBeInTheDocument();
    expect(resources.querySelector('p[aria-live="polite"]')).toHaveTextContent("Đồng bộ");
    expect(within(resources).getByText("Dữ liệu được đồng bộ từ CRM, kiểm tra tính nhất quán và chuẩn bị cho phân tích hấp thụ.")).toBeInTheDocument();
    expect(resources.querySelectorAll(".pipeline-node")).toHaveLength(6);
    expect(within(stageLegend).getAllByRole("listitem")).toHaveLength(6);
    expect(document.querySelector(".home-shell > style").textContent).toContain("translateX(calc(var(--agent-column) * 100%))");
  });

  it("moves the agent through every stage in order", () => {
    vi.useFakeTimers();
    try {
      renderPage();

      const resources = document.querySelector("#resources");
      expect(resources.querySelector('p[aria-live="polite"]')).toHaveTextContent("Đồng bộ");
      act(() => vi.advanceTimersByTime(3200));
      expect(resources.querySelector('p[aria-live="polite"]')).toHaveTextContent("Kiểm tra");
      expect(resources.querySelectorAll(".pipeline-node-completed")).toHaveLength(1);
      act(() => vi.advanceTimersByTime(800));
      expect(resources.querySelector('p[aria-live="polite"]')).toHaveTextContent("Phân tích");
      expect(resources.querySelectorAll(".pipeline-node-completed")).toHaveLength(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("stacks the data pipeline on narrow screens", () => {
    renderPage({ isNarrow: true, isMobile: true });

    expect(document.querySelector("#resources > div")).toHaveStyle({ gridTemplateColumns: "1fr" });
    expect(document.querySelector("#resources .pipeline-card")).toBeInTheDocument();
  });

  it("renders a truthful footer with only verified destinations", () => {
    renderPage();

    const footer = screen.getByRole("contentinfo");
    expect(within(footer).getByText("AbsorpIQ giúp đội ngũ bất động sản tập trung dữ liệu hiện có để theo dõi các chỉ báo hấp thụ và đưa ra quyết định rõ ràng hơn.")).toBeInTheDocument();
    expect(within(footer).getByRole("navigation", { name: "Nền tảng" })).toBeInTheDocument();
    expect(within(footer).getByRole("navigation", { name: "Khám phá" })).toBeInTheDocument();
    expect(within(footer).getByRole("navigation", { name: "Tài khoản" })).toBeInTheDocument();

    expect(within(footer).getAllByRole("link").map((link) => [link.textContent, link.getAttribute("href")])).toEqual([
      ["Tổng quan", "#overview"],
      ["Nền tảng", "#platform"],
      ["Phân tích", "#insights"],
      ["Tài nguyên", "#resources"],
      ["Yêu cầu bản demo", "#contact"],
      ["Dự án", "/projects"],
      ["Đăng nhập", "/login"],
    ]);
    expect(within(footer).queryByRole("link", { name: "Phương pháp" })).not.toBeInTheDocument();
    expect(within(footer).queryByRole("link", { name: /riêng tư|điều khoản/i })).not.toBeInTheDocument();
  });

  it("stacks footer navigation and legal bar on mobile", () => {
    renderPage({ isNarrow: true, isMobile: true });

    const footer = screen.getByRole("contentinfo");
    const footerTop = footer.firstElementChild;
    const footerLinks = footerTop?.children[1];
    const footerBottom = footer.lastElementChild;
    expect(footerTop).toHaveStyle({ gridTemplateColumns: "1fr" });
    expect(footerLinks).toHaveStyle({ gridTemplateColumns: "1fr" });
    expect(footerBottom).toHaveStyle({ flexDirection: "column" });
  });

  it("renders the CRM integration introduction without fabricated metrics or charts", () => {
    renderPage();

    const card = screen.getByRole("article", { name: "Dữ liệu CRM" });
    expect(within(card).getByText("Dữ liệu bán hàng cho góc nhìn dự án chính xác hơn")).toBeInTheDocument();
    expect(within(card).getByText("Dự án, phân khu, căn hộ và giao dịch từ Mini CRM được chuẩn hóa.", { exact: false })).toBeInTheDocument();
    expect(within(card).getByText("Dữ liệu dự án từ CRM")).toBeInTheDocument();
    expect(within(card).queryByText("412")).not.toBeInTheDocument();
    expect(within(card).queryByText("68,4%")).not.toBeInTheDocument();
    expect(card.querySelector(".miniChart")).toBeNull();
    expect(card.querySelector(".miniBar")).toBeNull();
    expect(within(card).queryByText(/thời gian thực/i)).not.toBeInTheDocument();

    expect(within(card).queryByRole("button")).not.toBeInTheDocument();
    expect(within(card).queryByRole("list")).not.toBeInTheDocument();
  });

  it("keeps the CRM introduction compact on mobile", () => {
    renderPage({ isNarrow: true, isMobile: true });

    const card = screen.getByRole("article", { name: "Dữ liệu CRM" });
    expect(card.parentElement).toHaveStyle({ width: "100%" });
    expect(within(card).queryByRole("button")).not.toBeInTheDocument();
    expect(within(card).queryByRole("list")).not.toBeInTheDocument();
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
