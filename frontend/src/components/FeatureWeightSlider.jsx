import React from "react";
import { color, radius, size, space } from "../styles/tokens";

export default function FeatureWeightSlider({ featureKey, spec, onChange }) {
  const weight = Number(spec?.weight || 0);
  return (
    <label style={S.row}>
      <span style={S.name}>{featureKey}</span>
      <input
        aria-label={`Trọng số ${featureKey}`}
        type="range"
        min="0"
        max="1"
        step="0.01"
        value={weight}
        onChange={(event) => onChange(Number(event.target.value))}
        style={S.range}
      />
      <output style={S.value}>{weight.toFixed(2)}</output>
    </label>
  );
}

const S = {
  row: { display: "grid", gridTemplateColumns: "minmax(150px, 1fr) minmax(140px, 2fr) 48px", gap: space(3), alignItems: "center", padding: `${space(3)}px 0`, borderBottom: `1px solid ${color.border}` },
  name: { color: color.body, fontSize: size.small, overflowWrap: "anywhere" },
  range: { accentColor: color.accent, width: "100%" },
  value: { padding: "5px 7px", background: color.canvas, borderRadius: radius.sm, color: color.ink, fontFamily: "monospace", fontSize: size.tiny, textAlign: "center" },
};
