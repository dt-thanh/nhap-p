# Hướng dẫn CI/CD Deploy — nhap-p

> Tài liệu này hướng dẫn cách hệ thống tự động deploy 2 frontend lên Cloudflare Pages thông qua GitHub Actions. Chỉ cần cài đặt **một lần**, sau đó cả nhóm push code là website tự cập nhật.

---

## Tổng quan

```
git push → GitHub Actions tự động chạy → build → deploy lên Cloudflare Pages
```

Repo có 2 frontend, mỗi cái có 1 workflow riêng:

| Frontend | Workflow file | Cloudflare project | URL sau deploy |
|----------|--------------|-------------------|----------------|
| `frontend/` (gốc) | `deploy-frontend.yml` | `af-frontend` | `https://af-frontend.pages.dev` |
| `crm-frontend/` | `deploy-crm.yml` | `crm-frontend` | `https://crm-frontend.pages.dev` |

Sửa code trong `frontend/` → chỉ workflow gốc chạy. Sửa trong `crm-frontend/` → chỉ workflow CRM chạy. Không ảnh hưởng nhau.

---

## Cài đặt lần đầu (1 người làm cho cả nhóm)

### Bước 1 — Tạo tài khoản Cloudflare

Vào [dash.cloudflare.com](https://dash.cloudflare.com), đăng ký miễn phí (không cần thẻ tín dụng).

### Bước 2 — Tạo 2 project Pages trên Cloudflare

Vào **Workers & Pages → Create → Pages → Direct Upload**, tạo lần lượt:

- Project tên **`af-frontend`** (cho `frontend/` gốc)
- Project tên **`crm-frontend`** (cho `crm-frontend/`)

Tạo xong để đó, không cần upload gì. Tên project phải khớp chính xác với tên trong file workflow.

### Bước 3 — Lấy Account ID và API Token

**Account ID:**
- Ở trang Workers & Pages, cột bên phải có sẵn "Account ID" → copy.

**API Token:**
- Vào **My Profile → API Tokens → Create Token**.
- Chọn mẫu **"Edit Cloudflare Workers"** (hoặc tạo custom token có quyền *Cloudflare Pages: Edit*).
- Copy chuỗi token (chỉ hiện 1 lần, mất thì phải tạo lại).

### Bước 4 — Nạp 2 secret vào GitHub

Vào repo `nhap-p` trên GitHub → **Settings → Secrets and variables → Actions → New repository secret**, tạo:

| Tên secret (đặt chính xác) | Giá trị |
|----------------------------|---------|
| `CLOUDFLARE_API_TOKEN` | Chuỗi token từ bước 3 |
| `CLOUDFLARE_ACCOUNT_ID` | Account ID từ bước 3 |

Cả 2 workflow dùng chung 2 secret này. Thành viên khác trong nhóm **không cần biết** và **không nhìn thấy** token — GitHub che đi.

### Bước 5 — Chỉnh nhánh deploy (nếu cần)

Mở 2 file workflow, dòng `branches: [main]` — đổi thành nhánh chính của repo (ví dụ `staging` nếu nhóm đang dùng nhánh đó).

### Bước 6 — Push các file CI/CD lên repo

```bash
git add .github/workflows/deploy-crm.yml
git add .github/workflows/deploy-frontend.yml
git add crm-frontend/public/_redirects
git add frontend/public/_redirects
git commit -m "ci: thêm GitHub Actions deploy 2 frontend lên Cloudflare Pages"
git push
```

Xong. Từ giờ mọi thay đổi push vào nhánh chính sẽ tự động deploy.

---

## Các file trong bộ CI/CD

```
nhap-p/
├── .github/workflows/
│   ├── deploy-crm.yml              ← Robot deploy crm-frontend/
│   └── deploy-frontend.yml         ← Robot deploy frontend/
├── crm-frontend/public/_redirects  ← Giúp link sâu /projects/123 không bị 404
└── frontend/public/_redirects      ← Tương tự cho frontend gốc
```

### deploy-crm.yml / deploy-frontend.yml làm gì?

Mỗi khi push code vào nhánh chính, robot chạy 5 bước:

```
① Checkout code từ repo
② Cài Node.js 20
③ npm ci (cài thư viện)
④ npm run build (biên dịch → thư mục dist/)
⑤ Đẩy dist/ lên Cloudflare Pages
```

Nếu bước ④ lỗi (code sai), robot dừng lại — website cũ vẫn nguyên, không bị hỏng.

### _redirects làm gì?

Chỉ có 1 dòng: `/*    /index.html   200`. Bảo Cloudflare trả `index.html` cho mọi URL, để react-router xử lý routing phía client. Không có file này thì bấm F5 hoặc gõ link trực tiếp sẽ bị 404.

---

## Dành cho thành viên nhóm

Thành viên mới **không cần** làm bất kỳ bước cài đặt CI/CD nào. Chỉ cần:

1. Được mời vào repo (Settings → Collaborators → Add people).
2. Clone repo, code bình thường.
3. Tạo nhánh → Push → Mở Pull Request → Merge vào nhánh chính.
4. Robot tự deploy. Xem kết quả ở tab **Actions** trên GitHub.

---

## Kiểm tra trạng thái deploy

- **Tab Actions trên GitHub:** Vòng tròn vàng = đang chạy, dấu tích xanh = thành công, dấu X đỏ = lỗi.
- **Cloudflare dashboard:** Vào Workers & Pages → chọn project → tab Deployments để xem lịch sử deploy.
- **Chạy tay (không cần push):** Trên tab Actions, chọn workflow → nút "Run workflow" → chạy lại lần deploy gần nhất.

---

## Xử lý lỗi thường gặp

**Build lỗi (dấu X đỏ trên Actions):**
Bấm vào workflow lỗi → đọc log → thường là lỗi TypeScript hoặc import sai. Sửa code → push lại.

**Deploy thành công nhưng link sâu bị 404:**
Kiểm tra file `public/_redirects` trong thư mục frontend tương ứng. Nếu thiếu, tạo lại với nội dung `/*    /index.html   200`.

**Lỗi "Authentication failed" ở bước deploy:**
Secret trên GitHub bị sai hoặc token Cloudflare hết hạn. Vào Settings → Secrets → cập nhật lại `CLOUDFLARE_API_TOKEN`.

---

## Sau này muốn mở rộng

**Thêm frontend thứ 3:** Copy 1 file workflow, đổi 3 chỗ — `name`, `paths` (thư mục watch), `--project-name`. Tạo thêm 1 project Pages trên Cloudflare. Xong.

**Thêm bước test tự động:** Thêm 1 step giữa bước Install và Build:
```yaml
- name: Test
  working-directory: crm-frontend
  run: npm test
```

**Deploy backend:** Backend là ứng dụng Python chạy server, không deploy lên Cloudflare Pages được (Pages chỉ nhận file tĩnh). Cần dịch vụ khác như Render, Railway, hoặc Fly.io — tạo workflow riêng khi backend sẵn sàng.

**Dùng tên miền riêng:** Vào Cloudflare Pages → project → Custom domains → thêm domain của bạn.

---

*Cập nhật: Tháng 8/2026*
