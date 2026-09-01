from __future__ import annotations


def test_single_compose_worker_owns_rq_scheduler(monkeypatch):
    import src.worker as worker

    calls = {}

    class FakeWorker:
        def __init__(self, queues, connection):
            calls["queues"] = queues
            calls["connection"] = connection

        def work(self, *, with_scheduler):
            calls["with_scheduler"] = with_scheduler

    monkeypatch.setattr(worker, "Worker", FakeWorker)
    monkeypatch.setattr(worker, "get_redis", lambda: "redis")

    worker.main()

    assert calls["with_scheduler"] is True
