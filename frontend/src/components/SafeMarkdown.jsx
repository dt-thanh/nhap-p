import React from "react";
import { color, radius, size, space, font } from "../styles/tokens";

// Small, dependency-free Markdown renderer for agent output. It creates React
// nodes only and deliberately does not support raw HTML.
export default function SafeMarkdown({ children }) {
  const lines = String(children || "").replace(/\r/g, "").split("\n");
  const blocks = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i].trim();
    if (!line) { i += 1; continue; }
    if (line.startsWith("|")) {
      const tableLines = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) tableLines.push(lines[i++].trim());
      if (tableLines.length > 1 && /^\|?[\s:|-]+\|?$/.test(tableLines[1])) {
        const rows = tableLines.filter((_, index) => index !== 1).map(parseRow);
        blocks.push(
          <div style={S.tableWrap} key={`table-${i}`}><table style={S.table}>
            <thead><tr>{rows[0].map((cell, index) => <th style={S.th} key={index}>{inline(cell)}</th>)}</tr></thead>
            <tbody>{rows.slice(1).map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, index) => <td style={S.td} key={index}>{inline(cell)}</td>)}</tr>)}</tbody>
          </table></div>,
        );
        continue;
      }
    }
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      const Tag = `h${heading[1].length + 2}`;
      blocks.push(<Tag key={i} style={S.heading}>{inline(heading[2])}</Tag>);
      i += 1; continue;
    }
    if (/^[-*]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^[-*]\s+/.test(lines[i].trim())) items.push(lines[i++].trim().replace(/^[-*]\s+/, ""));
      blocks.push(<ul key={`ul-${i}`} style={S.list}>{items.map((item, index) => <li key={index}>{inline(item)}</li>)}</ul>);
      continue;
    }
    if (/^\d+\.\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\d+\.\s+/.test(lines[i].trim())) items.push(lines[i++].trim().replace(/^\d+\.\s+/, ""));
      blocks.push(<ol key={`ol-${i}`} style={S.list}>{items.map((item, index) => <li key={index}>{inline(item)}</li>)}</ol>);
      continue;
    }
    blocks.push(<p key={i} style={S.p}>{inline(line)}</p>);
    i += 1;
  }
  return <div style={S.root}>{blocks}</div>;
}

function parseRow(line) {
  return line.replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());
}

function inline(text) {
  const parts = String(text).split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter(Boolean);
  return parts.map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={index}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`")) return <code key={index} style={S.code}>{part.slice(1, -1)}</code>;
    return <React.Fragment key={index}>{part}</React.Fragment>;
  });
}

const S = {
  root: { lineHeight: 1.58 },
  p: { margin: `0 0 ${space(2)}px` },
  heading: { margin: `${space(3)}px 0 ${space(2)}px`, color: color.ink, fontFamily: font.display, fontSize: size.body },
  list: { margin: `0 0 ${space(3)}px`, paddingLeft: space(5), display: "grid", gap: space(1) },
  code: { fontFamily: font.mono, fontSize: "0.9em", background: color.surface, padding: "1px 5px", borderRadius: radius.sm },
  tableWrap: { overflowX: "auto", margin: `${space(2)}px 0 ${space(3)}px`, border: `1px solid ${color.border}`, borderRadius: radius.sm },
  table: { width: "100%", borderCollapse: "collapse", fontSize: size.tiny, background: color.surface },
  th: { textAlign: "left", padding: `${space(2)}px ${space(3)}px`, background: color.canvas, color: color.ink, borderBottom: `1px solid ${color.border}` },
  td: { padding: `${space(2)}px ${space(3)}px`, borderBottom: `1px solid ${color.border}`, whiteSpace: "nowrap" },
};
