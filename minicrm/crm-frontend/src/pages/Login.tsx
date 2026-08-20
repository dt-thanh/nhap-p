import { useEffect, useState } from "react";
import { Navigate, useSearchParams } from "react-router-dom";
import { ArrowRight, Building2, LineChart, Users2, ShieldCheck } from "lucide-react";
import { useAuth } from "../context/AuthContext";

const FEATURES = [
  { icon: LineChart, title: "Hiệu suất bán hàng & hấp thụ theo thời gian thực", desc: "Trực quan tức thời để ra quyết định tự tin" },
  { icon: Users2, title: "Quản lý bán hàng & tồn kho tập trung", desc: "Hợp nhất dự án, sản phẩm, giao dịch và đội ngũ" },
  { icon: ShieldCheck, title: "An toàn. Tin cậy. Sẵn sàng tăng trưởng.", desc: "Bảo mật và khả năng mở rộng cấp doanh nghiệp" },
];

export function Login() {
  // CP4: KHÔNG còn ô email/mật khẩu. Mini CRM không nhận, không chuyển tiếp và
  // không nhìn thấy mật khẩu người dùng — Microsoft Entra giữ toàn bộ phần đó.
  // Một form mật khẩu ở đây, dù chỉ chuyển tiếp sang IdP, cũng dạy người dùng
  // thói quen gõ mật khẩu công ty vào một trang không phải của Microsoft.
  const { user, loading, login } = useAuth();
  const [params] = useSearchParams();
  const [error, setError] = useState("");

  useEffect(() => {
    const code = params.get("error");
    if (!code) return;
    setError(
      code === "NO_ROLE_ASSIGNED"
        ? "Tài khoản của bạn đã đăng nhập được nhưng chưa được cấp quyền trong Mini CRM. Liên hệ quản trị hệ thống để được gán app role."
        : "Đăng nhập không thành công. Vui lòng thử lại hoặc liên hệ quản trị hệ thống.",
    );
  }, [params]);

  if (loading) {
    return <div className="flex min-h-screen items-center justify-center text-ink-muted">Đang kiểm tra phiên đăng nhập…</div>;
  }
  if (user) return <Navigate to="/" replace />;

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
            {error && <p className="rounded-lg bg-status-redbg px-3 py-2 text-sm text-status-red">{error}</p>}

            <button
              onClick={() => login(params.get("return_to") ?? "/")}
              className="flex w-full items-center justify-center gap-2 rounded-lg bg-gradient-to-r from-gold to-gold-light py-3 text-sm font-semibold text-white transition-opacity hover:opacity-95"
            >
              <Building2 className="h-4 w-4" />
              Đăng nhập bằng tài khoản công ty
              <ArrowRight className="h-4 w-4" />
            </button>

            <p className="rounded-lg bg-surface-page px-3 py-2 text-center text-xs text-ink-muted">
              Bạn sẽ được chuyển sang trang đăng nhập của Microsoft. Mini CRM
              không lưu mật khẩu của bạn.
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
