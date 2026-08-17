/** Định dạng tiền tệ và số cho toàn app CRM. */
export function formatVND(amount: number): string {
  if (amount >= 1_000_000_000)
    return `${(amount / 1_000_000_000).toLocaleString("vi-VN", { maximumFractionDigits: 2 })} tỷ`;
  if (amount >= 1_000_000)
    return `${(amount / 1_000_000).toLocaleString("vi-VN", { maximumFractionDigits: 0 })} tr`;
  return amount.toLocaleString("vi-VN");
}
export function formatVNDFull(amount: number): string {
  return amount.toLocaleString("vi-VN") + " ₫";
}
export function formatNumber(n: number): string {
  return n.toLocaleString("vi-VN");
}
export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("vi-VN", { day: "2-digit", month: "2-digit", year: "numeric" });
}
