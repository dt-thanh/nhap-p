# Tài Liệu Đặc Tả Thuật Toán Xếp Hạng Phân Cấp (Hierarchical Absorption Scoring)

Tài liệu này đặc tả chi tiết về **Bản chất, Công thức Toán học, Cấu trúc Cấp độ (Grains)** và **Quy trình hoạt động** của thuật toán xếp hạng phân cấp mới (Hierarchical Absorption Scoring - triển khai từ PR-1 đến PR-5) trên nhánh này.

---

## 1. Bản Chất Của Thuật Toán

Thuật toán xếp hạng phân cấp (Hierarchical Absorption Scoring) là phương pháp đánh giá đa tiêu chí kết hợp tối ưu hóa trọng số nhằm xác định khả năng hấp thụ (mức độ ưu tiên bán) của từng căn hộ (unit).

Thay vì tính điểm phẳng trực tiếp từ các đặc trưng căn hộ, thuật toán mới chia mô hình đánh giá làm **4 cấp độ độc lập (Grains)**:
1.  **Vĩ mô (Market)**: Các chỉ số ảnh hưởng đến toàn bộ thị trường.
2.  **Dự án (Project)**: Các chỉ số cố định của toàn dự án.
3.  **Phân khu (Area)**: Các chỉ số vận hành và điểm chuyên gia của từng phân khu.
4.  **Căn hộ (Unit)**: Trạng thái và mức độ quan tâm trực tiếp đối với từng căn hộ.

Điểm số của từng cấp độ sau đó sẽ được tổng hợp lại thông qua một phép tổng có trọng số cấp cao nhất (`grain_weights`) để cho ra điểm số phân cấp cuối cùng (`hierarchical_score`).

> [!IMPORTANT]
> **Ràng buộc Con người Kiểm duyệt (Human-in-the-Loop)**
> Mọi thay đổi về cấu hình trọng số phân cấp hoặc các khuyến nghị do AI Agent đưa ra đều bắt buộc phải đi qua bước kiểm duyệt thủ công của con người (`pending_approval` -> `approved`) trước khi được áp dụng chính thức. Đây là ràng buộc cứng của dự án.

---

## 2. Vị Trí Của Thuật Toán Trong Mã Nguồn

| Thành phần | Đường dẫn tệp | Chức năng |
| :--- | :--- | :--- |
| **Động cơ tính điểm** | [`src/ranking/engine.py`](file:///d:/vinailab/Change/P-100/src/ranking/engine.py) | Hàm thuần (pure function) `score_unit` thực hiện phép tính tổng có trọng số (weighted sum). Được gọi để tính điểm cho từng cấp độ và tổng hợp cấp cao nhất. |
| **Toán học AHP** | [`src/ranking/ahp.py`](file:///d:/vinailab/Change/P-100/src/ranking/ahp.py) | Hàm thuần tính toán ma trận AHP, suy ra trọng số từ phán đoán so sánh cặp của chuyên gia và đo độ nhất quán (Consistency Ratio - CR). |
| **Phân nhóm (Bands)** | [`src/ranking/bands.py`](file:///d:/vinailab/Change/P-100/src/ranking/bands.py) | Phân cấp trình bày khả năng bán của điểm số phân cấp cuối cùng thành các nhóm `high` ($\ge 0.66$), `medium` ($0.33 \le \text{score} < 0.66$), `low` ($< 0.33$). |
| **Điều phối phân cấp** | [`src/ranking/service.py`](file:///d:/vinailab/Change/P-100/src/ranking/service.py) | Hàm `compute_hierarchical_scores_for_run` thực hiện thu thập dữ liệu các cấp độ (market, project, area, unit), tính điểm độc lập từng cấp và thực hiện phép tổng hợp phân cấp cuối cùng. |
| **Cấu hình Trọng số** | [`src/services/ranking_config.py`](file:///d:/vinailab/Change/P-100/src/services/ranking_config.py) | Hàm `validate_hierarchical_weights` dùng để kiểm tra tính hợp lệ của cấu trúc trọng số phân cấp trước khi lưu/xuất bản. |
| **Quy trình Duyệt** | [`src/services/governance.py`](file:///d:/vinailab/Change/P-100/src/services/governance.py) | Quản lý vòng đời phê duyệt các đề xuất thay đổi trọng số phân cấp. |

---

## 3. Cấu Trúc 4 Cấp Độ Đánh Giá (Grains)

Cấu hình trọng số của các cấp độ được khai báo chi tiết trong trường `hierarchical_weights` của bảng `ranking_configs`.

```json
{
  "market": {
    "market_interest_rate": {"weight": 1.0, "direction": "negative", "missing_value_policy": "neutral"}
  },
  "project": {
    "expert_location_score": {"weight": 1.0, "direction": "positive", "missing_value_policy": "neutral"}
  },
  "area": {
    "area_accessibility": {"weight": 0.5, "direction": "positive", "missing_value_policy": "neutral"},
    "area_velocity_norm": {"weight": 0.5, "direction": "positive", "missing_value_policy": "neutral"}
  },
  "grain_weights": {
    "market": {"weight": 0.10, "missing_value_policy": "skip"},
    "project": {"weight": 0.25, "missing_value_policy": "skip"},
    "area": {"weight": 0.25, "missing_value_policy": "skip"},
    "unit": {"weight": 0.40, "missing_value_policy": "skip"}
  }
}
```

### 3.1. Cấp độ Vĩ mô (Market)
Đo lường các yếu tố bên ngoài hệ thống bất động sản ảnh hưởng chung đến khả năng hấp thụ của thị trường.
*   **Đặc trưng tiêu biểu**: Lãi suất ngân hàng (`market_interest_rate`).
*   **Cơ chế**: Dữ liệu do chuyên gia cung cấp dưới dạng các phán đoán được phê duyệt và lưu vào snapshot tĩnh.

### 3.2. Cấp độ Dự án (Project)
Đo lường các đặc trưng mang tính chất dự án, áp dụng đồng nhất cho mọi căn hộ nằm trong dự án đó.
*   **Đặc trưng tiêu biểu**: Điểm vị trí địa lý của dự án (`expert_location_score`).
*   **Cơ chế**: Chuyên gia nhập liệu thông qua hệ thống Governance, lưu thành snapshot tĩnh tại mỗi lượt tính toán.

### 3.3. Cấp độ Phân khu (Area)
Đánh giá mức độ hấp dẫn của phân khu (khối nhà, tòa nhà) chứa căn hộ. Cấp độ này kết hợp cả hai nguồn dữ liệu:
*   **Dữ liệu CRM**: Tính toán tự động từ phễu giao dịch (`area_velocity_norm` - vận tốc giao dịch 30 ngày, `area_conversion_norm` - tỷ lệ chuyển đổi).
*   **Dữ liệu Chuyên gia**: Chỉnh sửa thủ công và phê duyệt qua hệ thống Governance, ví dụ: điểm giao thông kết nối (`area_accessibility`).

### 3.4. Cấp độ Căn hộ (Unit)
Đánh giá nội tại của chính căn hộ đó.
*   **Đặc trưng tiêu biểu**: Trạng thái sẵn sàng bán (`unit_available`), số lượng khách hàng quan tâm đang trong phễu giao dịch (`unit_demand_norm` - chuẩn hóa theo thang bão hòa tối đa 3 deal).

---

## 4. Công Thức Toán Học & Quy Trình Tính Toán

### 4.1. Xác Định Trọng Số Cấp Độ bằng AHP (Analytic Hierarchy Process)

Với mỗi cấp độ (Market, Project, Area), chuyên gia không nhập trọng số trực tiếp mà hệ thống suy ra thông qua ma trận so sánh cặp $A_{n \times n}$ trên thang Saaty 1-9.

#### A. Công thức suy ra trọng số (Row Geometric Mean Method - RGMM)
Sử dụng trung bình nhân theo hàng để đảm bảo kết quả tất định và nhất quán trên mọi môi trường:
$$w_i = \frac{\exp\left( \frac{1}{n} \sum_{j=1}^n \ln(a_{ij}) \right)}{\sum_{k=1}^n \exp\left( \frac{1}{n} \sum_{j=1}^n \ln(a_{kj}) \right)}$$

Hệ thống làm tròn trọng số về 4 chữ số thập phân, phần dư làm tròn được dồn vào tiêu chí có trọng số lớn nhất.

#### B. Kiểm tra tính nhất quán (Consistency Ratio - CR)
Để phát hiện phán đoán mâu thuẫn của chuyên gia:
$$\lambda_{max} = \frac{1}{n} \sum_{i=1}^n \frac{(A \cdot w)_i}{w_i} \implies CI = \frac{\lambda_{max} - n}{n - 1} \implies CR = \frac{CI}{RI(n)}$$
*   **RI(n)**: Chỉ số ngẫu nhiên của Saaty tra bảng theo số lượng tiêu chí $n$.
*   **Quy tắc duyệt CR**:
    - $CR \le \text{ngưỡng}$ (ví dụ: $0.08$ với $n=4$): Phê duyệt tự động.
    - $\text{ngưỡng} < CR \le 0.20$: Bắt buộc phê duyệt ghi đè (`override=true`) kèm lý do ghi nhận vào tệp nhật ký `note`.
    - $CR > 0.20$: Từ chối phán đoán hoàn toàn, yêu cầu sửa đổi tại các ô phán đoán lệch nhiều nhất (hotspots).

---

### 4.2. Tính Điểm Các Cấp Độ (Grains Score)

Với mỗi cấp độ $g \in \{market, project, area, unit\}$, điểm số độc lập $Score_g$ được tính dựa trên giá trị đặc trưng đã được định hướng theo chiều $\text{oriented}(v_i, d_i)$ (trong đó $d_i \in \{positive, negative\}$):

$$\text{oriented}(v_i, d_i) = \begin{cases} v_i & d_i = \text{positive} \\ 1 - v_i & d_i = \text{negative} \end{cases}$$

$$\text{Score}_g = \frac{\sum_{i \in \text{computed}_g} w_i \cdot \text{oriented}(v_i, d_i)}{\sum_{i \in \text{computed}_g} w_i}$$

Nếu một đặc trưng bị thiếu, hệ thống áp dụng chính sách `missing_value_policy`:
-   `zero`: đưa giá trị về $0$.
-   `neutral`: đưa giá trị về $0.5$.
-   `skip`: bỏ qua đặc trưng này (mẫu số giảm đi một lượng bằng trọng số của đặc trưng).

---

### 4.3. Tổng Hợp Điểm Phân Cấp Cuối Cùng (`hierarchical_score`)

Sau khi tính được điểm của từng cấp độ riêng lẻ, điểm xếp hạng phân cấp cuối cùng của căn hộ được tổng hợp bằng một phép tổng có trọng số cấp cao nhất:

$$\text{Hierarchical Score} = \frac{\sum_{g \in \text{active}} W_g \cdot \text{Score}_g}{\sum_{g \in \text{active}} W_g}$$

Trong đó:
-   $\text{active}$: Các cấp độ có điểm số khả dụng (không bị loại trừ).
-   $W_g$: Trọng số của cấp độ đó được cấu hình trong `grain_weights`.
-   Nếu một cấp độ cha bị thiếu dữ liệu (ví dụ: dự án chưa được chấm điểm vị trí hoặc thị trường chưa cập nhật lãi suất) và chính sách thiếu của nó là `skip`, cấp độ đó sẽ không tham gia vào phép tính. Trọng số của các cấp độ còn lại sẽ tự động chuẩn hóa để bù đắp phần thiếu hụt.

---

### 4.4. Bốn Chế Độ Tính Điểm (Score Modes)

Dựa trên tính khả dụng của dữ liệu tại thời điểm tính toán, hệ thống ghi nhận trạng thái `score_mode` vào tệp nhật ký đóng góp của từng căn hộ:

1.  **`unit_only`**:
    - Xảy ra khi cả 3 cấp độ cha (Market, Project, Area) đều không khả dụng.
    - Điểm phân cấp sẽ giảm về đúng bằng điểm nội tại của căn hộ ($Score_{unit}$).
2.  **`partial_hierarchical`**:
    - Xảy ra khi chỉ có 1 hoặc 2 trong số 3 cấp độ cha khả dụng.
3.  **`full_hierarchical`**:
    - Đầy đủ thông tin của cả 4 cấp độ cùng tham gia tính điểm.
4.  **`legal_gated`**:
    - Khi dự án bị đánh dấu rủi ro pháp lý cao (`HIGH_RISK`), hệ thống sẽ kích hoạt cổng khóa pháp lý. Căn hộ sẽ **không được xếp hạng** (điểm số = `NULL`, phân nhóm = `NULL`).

---

## 5. Quy Tắc Xếp Hạng & Phân Nhóm Tất Định

### 5.1. Xếp Hạng & Phá Hòa (Tie-break)
Các căn hộ hợp lệ được sắp xếp giảm dần theo điểm `hierarchical_score`. Trong trường hợp bằng điểm, hệ thống phá hòa theo thứ tự:
$$\text{Hierarchical Score (Giảm dần)} \longrightarrow \text{created\_at (Tăng dần)} \longrightarrow \text{unit\_id (Tăng dần)}$$
*   `created_at`: Thời điểm căn hộ được giám sát. Căn hộ giám sát sớm hơn xếp trước.
*   `unit_id`: ID của căn hộ để đảm bảo thứ tự hoàn toàn tất định giữa các lần chạy.

### 5.2. Phân Nhóm Khả Năng Bán (Bands)
Điểm số phân cấp cuối cùng được ánh xạ thành 3 nhóm khả năng bán dựa trên các ngưỡng tuyệt đối:
*   **`high`**: Điểm số $\ge 0.66$
*   **`medium`**: Điểm số từ $0.33$ đến dưới $0.66$
*   **`low`**: Điểm số dưới $0.33$
*   Nếu căn hộ không đủ dữ liệu tính điểm (do tổng trọng số các đặc trưng khả dụng của cấp độ Unit $< 0.5$), phân nhóm trả về là `None`.
