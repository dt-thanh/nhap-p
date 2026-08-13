# GitFlow & Commit Flow Guidelines

## Mục tiêu

Thiết lập quy trình Git thống nhất giúp phát triển an toàn, dễ review và
dễ rollback.

## Mô hình nhánh

-   `develop`: Mã nguồn production, chỉ nhận merge từ release/hotfix.
-   `staging`: Nhánh code chính, dùng để test mọi thứ trên nhánh này.
-   `feature/<ten-tinh-nang>`: Phát triển tính năng mới.
-   `fix/<ten-loi>`: Sửa lỗi.
-   `release/<version>`: Chuẩn bị phát hành.
-   `hotfix/<ten-loi>`: Sửa lỗi khẩn trên production.

``` text
develop
 ├── hotfix/*
 │
 └── staging
      ├── feature/*
      ├── fix/*
      └── release/*
```

## Quy trình làm việc

1.  Đồng bộ:

``` bash
git switch develop
git pull origin develop
```

2.  Tạo branch:

``` bash
git switch -c feature/user-login
```

3.  Phát triển và commit nhỏ.

4.  Commit theo Conventional Commits:

-   feat: thêm tính năng
-   fix: sửa lỗi
-   docs: tài liệu
-   refactor: tái cấu trúc
-   test: kiểm thử
-   chore: bảo trì

Ví dụ:

``` text
feat(auth): add JWT authentication
fix(api): handle null response
docs: update README
```

5.  Push:

``` bash
git push -u origin feature/user-login
```

6.  Tạo Pull Request vào `develop`.

7.  Code review:

-   CI pass
-   Không còn conflict
-   Có ít nhất 1 approval
-   Test thành công

8.  Merge và xóa branch.

## Quy tắc commit

-   Một commit = một thay đổi logic.
-   Không commit code lỗi.
-   Message ở thì hiện tại.
-   Không dùng "update", "fix bug" chung chung.

## Commit Flow

``` text
Pull develop
    ↓
Create feature branch
    ↓
Code
    ↓
git add
    ↓
git commit
    ↓
git push
    ↓
Pull Request
    ↓
Review
    ↓
Merge develop
```

## Xử lý conflict

``` bash
git switch develop
git pull origin develop
git switch feature/user-login
git merge develop
```

Giải quyết conflict, test lại rồi push.

## Checklist trước PR

-   [ ] Build thành công
-   [ ] Test pass
-   [ ] Không còn file debug
-   [ ] Commit message đúng chuẩn
-   [ ] Đã cập nhật từ develop
-   [ ] PR có mô tả rõ ràng

## Quy ước đặt tên branch

-   feature/login
-   feature/chatbot
-   fix/token-expired
-   hotfix/payment-timeout
-   docs/setup-guide
-   refactor/database

## Best Practices

-   Commit thường xuyên.
-   Không làm nhiều tính năng trên một branch.
-   Không force push lên develop hoặc main.
-   Review trước khi merge.
-   Squash commit nếu lịch sử quá lộn xộn.
