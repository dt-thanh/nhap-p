import React from "react";
import Icon from "./ui/Icon";
import { color, font, size, space } from "../styles/tokens";

export default function EmptyState({ message = "Chưa có dữ liệu xếp hạng", icon = "inbox" }) {
  return (
    <div className="ranking-empty-state" role="status">
      <span className="ranking-empty-icon"><Icon name={icon} size={22} color={color.accent} /></span>
      <p style={{ margin: 0, color: color.muted, fontFamily: font.sans, fontSize: size.small }}>{message}</p>
      <span style={{ color: color.muted, fontSize: size.tiny }}>Thử đổi phạm vi hoặc bộ lọc để xem thêm dữ liệu.</span>
      <span style={{ display: "block", height: space(1) }} aria-hidden="true" />
    </div>
  );
}
