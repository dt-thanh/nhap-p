"""Phase 6: động cơ xếp hạng tối thiểu.

`engine.py` là hàm THUẦN (không I/O, không mạng) implement công thức §10.1 của
`docs/ranking/implementation_plan.md`. `service.py` đọc DB, gọi `engine.py`, ghi
`feature_snapshots`/`ranking_runs`/`ranking_scores`.

Đây KHÔNG phải động cơ đầy đủ của tài liệu trên — không worker RQ, không cò kích
hoạt sau sync, không endpoint khảo sát. Xem docstring của
`alembic/versions/0018_agent_recommendations.py` và `src/ranking/service.py` cho
phạm vi chính xác.
"""
