import React, { useEffect, useRef, useState } from "react";
import Icon from "./ui/Icon";
import "./RankingSearchBar.css";

export function filterRankingUnits(units = [], searchTerm = "") {
  const term = String(searchTerm || "").trim().toLocaleLowerCase("vi");

  return units.filter((unit) => {
    const searchable = [unit?.unit_code, unit?.unit_name, unit?.name]
      .filter(Boolean)
      .join(" ")
      .toLocaleLowerCase("vi");
    return !term || searchable.includes(term);
  });
}

export function RankingSearchBar({ onFilter, totalUnits = 0, resetKey = "" }) {
  const [searchTerm, setSearchTerm] = useState("");
  const [resultCount, setResultCount] = useState(totalUnits);
  const previousResetKey = useRef(resetKey);
  const skipNextDebounce = useRef(false);

  useEffect(() => {
    if (previousResetKey.current === resetKey) return;
    previousResetKey.current = resetKey;
    skipNextDebounce.current = true;
    setSearchTerm("");
    setResultCount(totalUnits);
    onFilter?.("");
  }, [onFilter, resetKey, totalUnits]);

  useEffect(() => {
    if (skipNextDebounce.current) {
      skipNextDebounce.current = false;
      return undefined;
    }
    const timer = window.setTimeout(() => {
      const nextCount = onFilter?.(searchTerm);
      if (typeof nextCount === "number") setResultCount(nextCount);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [onFilter, searchTerm]);

  return (
    <div className="ranking-search-bar" role="search" aria-label="Tìm kiếm xếp hạng">
      <div className="search-input-wrapper">
        <Icon name="search" size={20} color="var(--ranking-muted, #707070)" />
        <label className="sr-only" htmlFor="ranking-unit-search">Tìm căn</label>
        <input
          id="ranking-unit-search"
          type="search"
          value={searchTerm}
          onChange={(event) => setSearchTerm(event.target.value)}
          placeholder="Tìm căn (mã, tên)..."
          className="search-input"
        />
        {searchTerm && (
          <button
            type="button"
            onClick={() => setSearchTerm("")}
            className="clear-btn"
            aria-label="Xóa tìm kiếm"
          >
            ×
          </button>
        )}
      </div>
      <div className="result-count" aria-live="polite">
        <strong>{resultCount}</strong> / {totalUnits} căn
      </div>
    </div>
  );
}

export default RankingSearchBar;
