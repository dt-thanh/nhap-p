import { useEffect, useState } from "react";

/**
 * Khung frontend tối thiểu: xác nhận React gọi được backend qua compose network.
 * Các màn hình thật (UploadPage, AbsorptionChart, ForecastCard, ProposalInbox)
 * sẽ dựng theo SRS §5.2–5.4.
 */
export default function App() {
  const [health, setHealth] = useState({ state: "loading" });

  useEffect(() => {
    fetch("/health")
      .then((r) => r.json())
      .then((data) => setHealth({ state: "ok", data }))
      .catch((err) => setHealth({ state: "error", message: String(err) }));
  }, []);

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 640 }}>
      <h1>AbsorptionForecast AI Agent</h1>
      <p>Frontend đã chạy. Trạng thái kết nối backend:</p>
      <pre
        style={{
          background: "#f1f3f5",
          padding: "1rem",
          borderRadius: 8,
          overflowX: "auto",
        }}
      >
        {health.state === "loading" && "Đang kiểm tra /health..."}
        {health.state === "ok" && JSON.stringify(health.data, null, 2)}
        {health.state === "error" && `Không gọi được backend: ${health.message}`}
      </pre>
    </main>
  );
}
