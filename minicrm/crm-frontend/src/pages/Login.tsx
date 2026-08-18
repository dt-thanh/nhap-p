import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Mail, Lock, Eye, EyeOff, ArrowRight, Building2, LineChart, Users2, ShieldCheck } from "lucide-react";
import { useAuth } from "../context/AuthContext";

const FEATURES = [
  { icon: LineChart, title: "Hiệu suất bán hàng & hấp thụ theo thời gian thực", desc: "Trực quan tức thời để ra quyết định tự tin" },
  { icon: Users2, title: "Quản lý bán hàng & tồn kho tập trung", desc: "Hợp nhất dự án, sản phẩm, giao dịch và đội ngũ" },
  { icon: ShieldCheck, title: "An toàn. Tin cậy. Sẵn sàng tăng trưởng.", desc: "Bảo mật và khả năng mở rộng cấp doanh nghiệp" },
];

export function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [remember, setRemember] = useState(true);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit() {
    setError("");
    if (!email.trim()) return setError("Vui lòng nhập email");
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return setError("Email không hợp lệ");
    if (!password) return setError("Vui lòng nhập mật khẩu");
    setLoading(true);
    try {
      await login(email, password, remember);
      navigate("/", { replace: true });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen bg-surface-page">
      {/* Panel trái — branding + hero */}
      <div className="relative hidden w-[46%] flex-col justify-between overflow-hidden bg-navy-900 px-12 py-14 lg:flex">
        <div className="relative z-10 flex flex-col items-center text-center">
          <div className="flex h-20 w-20 items-center justify-center rounded-xl border-2 border-gold font-display text-2xl font-bold text-gold">AF</div>
          <h1 className="mt-5 font-display text-3xl font-semibold text-white">AbsorptionForecast</h1>
          <p className="font-display text-lg font-semibold tracking-wide text-gold">CRM</p>
          <p className="mt-4 max-w-xs text-sm leading-relaxed text-white/70">
            Giải pháp bán hàng bất động sản thông minh cho nhà phát triển hiện đại
          </p>
        </div>

        <div className="relative z-10 space-y-5">
          {FEATURES.map((f) => (
            <div key={f.title} className="flex items-start gap-3">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-teal/20 text-teal">
                <f.icon className="h-5 w-5" />
              </div>
              <div>
                <p className="text-sm font-semibold text-white">{f.title}</p>
                <p className="text-xs text-white/55">{f.desc}</p>
              </div>
            </div>
          ))}
        </div>

        <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-navy-900/40 via-navy-900/70 to-navy-900" />
        <div
          className="pointer-events-none absolute inset-x-0 bottom-0 h-1/2 bg-cover bg-center opacity-40"
          style={{ backgroundImage: "url('https://images.unsplash.com/photo-1512453979798-5ea266f8880c?auto=format&fit=crop&w=900&q=70')" }}
        />
      </div>

      {/* Panel phải — form */}
      <div className="flex flex-1 flex-col items-center justify-center px-6 py-12">
        <div className="w-full max-w-md rounded-2xl border border-line bg-white p-8 shadow-card sm:p-10">
          <h2 className="font-display text-3xl font-bold text-ink">Chào mừng trở lại</h2>
          <p className="mt-1 text-sm text-ink-muted">Đăng nhập để tiếp tục vào AbsorptionForecast CRM</p>

          <div className="mt-7 space-y-5">
            <div>
              <label className="mb-1.5 block text-sm font-semibold text-ink">Email</label>
              <div className="relative">
                <Mail className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-faint" />
                <input value={email} onChange={(e) => setEmail(e.target.value)} onKeyDown={(e) => e.key === "Enter" && handleSubmit()} className="input pl-10" placeholder="you@company.com" />
              </div>
            </div>

            <div>
              <label className="mb-1.5 block text-sm font-semibold text-ink">Mật khẩu</label>
              <div className="relative">
                <Lock className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-faint" />
                <input type={showPw ? "text" : "password"} value={password} onChange={(e) => setPassword(e.target.value)} onKeyDown={(e) => e.key === "Enter" && handleSubmit()} className="input px-10" placeholder="Nhập mật khẩu" />
                <button type="button" onClick={() => setShowPw((v) => !v)} className="absolute right-3 top-1/2 -translate-y-1/2 text-ink-faint hover:text-ink" aria-label="Hiện/ẩn mật khẩu">
                  {showPw ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </div>

            <div className="flex items-center justify-between">
              <label className="flex cursor-pointer items-center gap-2 text-sm text-ink">
                <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} className="h-4 w-4 rounded border-line-strong text-teal focus:ring-teal" />
                Ghi nhớ đăng nhập
              </label>
              <button className="text-sm font-semibold text-teal hover:underline">Quên mật khẩu?</button>
            </div>

            {error && <p className="rounded-lg bg-status-redbg px-3 py-2 text-sm text-status-red">{error}</p>}

            <button onClick={handleSubmit} disabled={loading} className="flex w-full items-center justify-center gap-2 rounded-lg bg-gradient-to-r from-gold to-gold-light py-3 text-sm font-semibold text-white transition-opacity hover:opacity-95 disabled:opacity-60">
              {loading ? "Đang đăng nhập…" : "Đăng nhập"}
              {!loading && <ArrowRight className="h-4 w-4" />}
            </button>

            <div className="flex items-center gap-3">
              <div className="h-px flex-1 bg-line" />
              <span className="text-xs text-ink-faint">hoặc tiếp tục với</span>
              <div className="h-px flex-1 bg-line" />
            </div>

            <button className="btn-ghost w-full py-3">
              <Building2 className="h-4 w-4" /> Đăng nhập bằng SSO
            </button>

            <p className="rounded-lg bg-surface-page px-3 py-2 text-center text-xs text-ink-muted">
              Chế độ demo: nhập email bất kỳ và mật khẩu ≥ 6 ký tự để vào.
            </p>
          </div>
        </div>

        <p className="mt-6 text-xs text-ink-faint">© 2024 AbsorptionForecast CRM. Bảo lưu mọi quyền.</p>
        <div className="mt-2 flex gap-4 text-xs font-medium text-ink-muted">
          <span>Chính sách bảo mật</span><span className="text-gold">•</span>
          <span>Điều khoản dịch vụ</span><span className="text-gold">•</span>
          <span>Hỗ trợ</span>
        </div>
      </div>
    </div>
  );
}
