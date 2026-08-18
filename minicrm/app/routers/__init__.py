"""Router HTTP của Mini CRM.

Router cố tình MỎNG: đọc request, gọi `app.crud`, ánh xạ lỗi sang mã HTTP. Toàn
bộ quyết định nghiệp vụ — thứ tự ghi/commit/gửi, quy tắc phiên bản, điều kiện
giao dịch đi sau căn — nằm ở `app/crud.py`. Cùng một lý do với router của backend:
logic nằm trong router thì không test được nếu không dựng cả tầng HTTP.
"""
