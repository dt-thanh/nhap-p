import { useEffect, useState } from "react";
import { Navigate, useSearchParams } from "react-router-dom";
import {
  ArrowRight,
  Building2,
  LineChart,
  Users2,
  ShieldCheck,
} from "lucide-react";
import { LOGOUT_FLAG_KEY, useAuth } from "../context/AuthContext";

const FEATURES = [
  {
    icon: LineChart,
    title: "Hiệu suất bán hàng & hấp thụ theo thời gian thực",
    desc: "Trực quan tức thời để ra quyết định tự tin",
  },
  {
    icon: Users2,
    title: "Quản lý bán hàng & tồn kho tập trung",
    desc: "Hợp nhất dự án, sản phẩm, giao dịch và đội ngũ",
  },
  {
    icon: ShieldCheck,
    title: "An toàn. Tin cậy. Sẵn sàng tăng trưởng.",
    desc: "Bảo mật và khả năng mở rộng cấp doanh nghiệp",
  },
];

export function Login() {
  // Mini CRM không thu thập hoặc chuyển tiếp mật khẩu.
  // Keycloak chịu trách nhiệm xác thực người dùng.
  const { user, loading, login, loginWithToken } = useAuth();
  const [params] = useSearchParams();
  const [error, setError] = useState("");
  const [loggedOutMessage, setLoggedOutMessage] = useState("");
  const [tokenInput, setTokenInput] = useState("");
  const [submittingToken, setSubmittingToken] = useState(false);
  const enableLegacyTokenLogin = import.meta.env.VITE_ENABLE_LEGACY_TOKEN_LOGIN === "true";

  useEffect(() => {
    const code = params.get("error");

    if (!code) {
      return;
    }

    setError(
      code === "NO_ROLE_ASSIGNED"
        ? "Tài khoản của bạn đã đăng nhập được nhưng chưa được cấp quyền trong Mini CRM. Liên hệ quản trị hệ thống để được gán app role."
        : "Đăng nhập không thành công. Vui lòng thử lại hoặc liên hệ quản trị hệ thống.",
    );
  }, [params]);

  useEffect(() => {
    // Đặt bởi `AuthContext.logout()` NGAY TRƯỚC khi điều hướng.
    // `sessionStorage` sống sót qua chuỗi redirect (kể cả sang origin Keycloak
    // rồi quay lại) trong cùng tab, nên đây là kênh DUY NHẤT còn nguyên vẹn để
    // biết trang này được tải ra SAU một lần đăng xuất vừa xảy ra.
    let flag: string | null = null;
    try {
      flag = sessionStorage.getItem(LOGOUT_FLAG_KEY);
      if (flag) sessionStorage.removeItem(LOGOUT_FLAG_KEY);
    } catch {
      return;
    }
    if (flag === "all") {
      setLoggedOutMessage("Đã đăng xuất khỏi mọi thiết bị thành công.");
    } else if (flag === "single") {
      setLoggedOutMessage("Đăng xuất thành công.");
    }
  }, []);

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center text-ink-muted">
        Đang kiểm tra phiên đăng nhập…
      </div>
    );
  }

  if (user) {
    return <Navigate to="/" replace />;
  }

  return (
    <div className="flex min-h-screen bg-surface-page">
      {/* Panel trái — branding và giới thiệu */}
      <div className="relative hidden w-[48%] flex-col justify-between overflow-hidden bg-navy-900 px-12 py-12 lg:flex xl:px-16">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_15%_10%,rgba(37,99,235,.42),transparent_38%),radial-gradient(circle_at_90%_75%,rgba(14,165,233,.22),transparent_36%)]" />
        <div className="pointer-events-none absolute inset-0 opacity-[0.055] [background-image:linear-gradient(rgba(255,255,255,.8)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,.8)_1px,transparent_1px)] [background-size:36px_36px]" />

        <div className="relative z-10">
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-primary to-sky-400 font-display text-lg font-extrabold text-white shadow-[0_14px_36px_rgba(37,99,235,.38)]">
            AF
          </div>

          <h1 className="mt-5 font-display text-[32px] font-bold tracking-[-0.04em] text-white">
            AbsorptionForecast
          </h1>

          <p className="mt-1 text-xs font-semibold uppercase tracking-[0.22em] text-blue-300">
            Mini CRM Workspace
          </p>

          <p className="mt-5 max-w-md text-[15px] leading-7 text-white/60">
            Một không gian tập trung để đội ngũ kinh doanh quản lý dự án, tồn kho
            và tiến độ giao dịch rõ ràng hơn mỗi ngày.
          </p>
        </div>

        <div className="relative z-10 my-10 space-y-3">
          {FEATURES.map((feature) => {
            const Icon = feature.icon;

            return (
              <div key={feature.title} className="flex items-start gap-3.5 rounded-2xl border border-white/[0.08] bg-white/[0.045] p-4 backdrop-blur-sm">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-blue-400/15 text-blue-300 ring-1 ring-inset ring-blue-300/10">
                  <Icon className="h-5 w-5" />
                </div>

                <div>
                  <p className="text-sm font-semibold leading-5 text-white">
                    {feature.title}
                  </p>
                  <p className="mt-1 text-xs leading-5 text-white/45">{feature.desc}</p>
                </div>
              </div>
            );
          })}
        </div>

        <p className="relative z-10 text-xs text-white/35">Được bảo vệ bởi Keycloak SSO · Phiên đăng nhập HttpOnly</p>
      </div>

      {/* Panel phải — đăng nhập */}
      <div className="relative flex flex-1 flex-col items-center justify-center overflow-hidden px-6 py-12">
        <div className="pointer-events-none absolute -right-24 -top-24 h-80 w-80 rounded-full bg-primary/[0.055] blur-3xl" />
        <div className="relative w-full max-w-md rounded-[24px] border border-white bg-white p-8 shadow-panel ring-1 ring-line sm:p-10">
          <div className="mb-8 flex items-center gap-3 lg:hidden">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary font-display text-sm font-bold text-white">AF</div>
            <div><p className="font-display text-sm font-bold text-ink">AbsorptionForecast</p><p className="text-[10px] uppercase tracking-[0.15em] text-primary">Mini CRM</p></div>
          </div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-primary">Đăng nhập hệ thống</p>
          <h2 className="mt-2 font-display text-[30px] font-bold tracking-[-0.04em] text-ink">
            Chào mừng trở lại
          </h2>

          <p className="mt-2 text-sm leading-6 text-ink-muted">
            Đăng nhập để tiếp tục vào AbsorptionForecast CRM
          </p>

          <div className="mt-8 space-y-5">
            {error && (
              <p className="rounded-lg bg-status-redbg px-3 py-2 text-sm text-status-red">
                {error}
              </p>
            )}

            {loggedOutMessage && (
              <p className="rounded-lg bg-status-greenbg px-3 py-2 text-sm text-status-green">
                {loggedOutMessage}
              </p>
            )}

            {enableLegacyTokenLogin && (
              <form
                onSubmit={async (e) => {
                  e.preventDefault();
                  if (!tokenInput.trim()) return;
                  setSubmittingToken(true);
                  setError("");
                  try {
                    await loginWithToken(tokenInput.trim());
                  } catch (err) {
                    setError("Token không hợp lệ hoặc đã hết hạn.");
                  } finally {
                    setSubmittingToken(false);
                  }
                }}
                className="space-y-3 border-b border-line pb-5 mb-5"
              >
                <div className="flex flex-col gap-1.5">
                  <label htmlFor="token-input" className="text-xs font-semibold text-ink-muted">
                    Đăng nhập bằng Token vai trò
                  </label>
                  <input
                    id="token-input"
                    type="text"
                    value={tokenInput}
                    onChange={(e) => setTokenInput(e.target.value)}
                    placeholder="Dán token mca_..., mcv_... hoặc mco_..."
                    disabled={submittingToken}
                    className="flex h-11 w-full rounded-xl border border-line bg-surface px-3 py-2 text-sm text-ink placeholder-ink-faint focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary disabled:bg-surface-raised"
                  />
                </div>
                <button
                  type="submit"
                  disabled={submittingToken || !tokenInput.trim()}
                  className="flex h-11 w-full items-center justify-center rounded-xl bg-ink text-sm font-semibold text-white shadow-float transition hover:-translate-y-0.5 disabled:opacity-50 disabled:transform-none"
                >
                  {submittingToken ? "Đang xác thực..." : "Xác nhận đăng nhập"}
                </button>
              </form>
            )}

            <button
              type="button"
              onClick={() => login(params.get("return_to") ?? "/")}
              className="flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-primary to-blue-500 text-sm font-semibold text-white shadow-float transition duration-150 hover:-translate-y-0.5 hover:shadow-panel"
            >
              <Building2 className="h-4 w-4" />
              Đăng nhập bằng tài khoản công ty
              <ArrowRight className="h-4 w-4" />
            </button>

            <p className="rounded-xl border border-line bg-surface-raised px-4 py-3 text-center text-xs leading-5 text-ink-muted">
              Bạn sẽ được chuyển sang trang đăng nhập an toàn của Keycloak.
              Mini CRM không lưu mật khẩu của bạn.
            </p>
          </div>
        </div>

        <p className="relative mt-6 text-xs text-ink-faint">
          © {new Date().getFullYear()} AbsorptionForecast CRM. Bảo lưu mọi quyền.
        </p>

        <div className="relative mt-2 flex flex-wrap justify-center gap-3 text-xs font-medium text-ink-muted">
          <span>Chính sách bảo mật</span>
          <span className="text-gold">•</span>
          <span>Điều khoản dịch vụ</span>
          <span className="text-gold">•</span>
          <span>Hỗ trợ</span>
        </div>
      </div>
    </div>
  );
}
