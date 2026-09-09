from dataclasses import replace
from uuid import UUID

import pytest

from scripts.restructure_existing_documents import (
    DatabaseIdentity,
    ReconstructionPlan,
    SourceIdentity,
    SourceProgress,
    TransitionError,
    reconstruct,
)


def source(number=1):
    return SourceIdentity(
        UUID(int=number),
        UUID(int=10),
        UUID(int=20),
        UUID(int=30),
        UUID(int=40),
        None,
        "a" * 64,
        "b" * 64,
    )


class Adapter:
    identity = DatabaseIdentity("synthetic-cluster", "synthetic-db", "42")

    def __init__(self):
        self.sources = (source(),)
        self.progress = SourceProgress("MISSING")
        self.calls = []
        self.outside = False

    def verify(self, plan, *, source=None):
        expected = plan.sources if source is None else (source,)
        actual = (
            self.sources
            if source is None
            else tuple(item for item in self.sources if item.batch_item_id == source.batch_item_id)
        )
        if self.identity != plan.database or actual != expected:
            raise TransitionError("SOURCE_CHANGED")
        if self.outside:
            raise TransitionError("UNAPPROVED_SOURCE")

    def observe(self, item):
        return self.progress

    def prepare(self, item):
        self.calls.append("prepare")
        self.progress = SourceProgress("PREPARED", UUID(int=50), "MISSING")

    def metadata(self, generation):
        self.calls.append("metadata_prepare")
        self.progress = SourceProgress("PREPARED", generation, "PREPARED", components=1)

    def project(self, stage):
        self.calls.append(stage)
        return 0


def plan(adapter):
    return ReconstructionPlan(adapter.identity, adapter.sources)


def test_local_path_resumes_without_repreparing_and_has_no_provider_stages():
    adapter = Adapter()
    first = reconstruct(plan(adapter), adapter=adapter)
    assert first.status == "LOCAL_RECONSTRUCTION_COMPLETE"
    assert first.prepared == 1 and first.metadata_components == 1
    assert adapter.calls[:2] == ["prepare", "metadata_prepare"]
    assert all("provider" not in stage for stage in adapter.calls)
    adapter.calls.clear()
    assert reconstruct(plan(adapter), adapter=adapter).status == first.status
    assert "prepare" not in adapter.calls and "metadata_prepare" not in adapter.calls
    assert str(source().batch_item_id) not in repr(first)


@pytest.mark.parametrize("kind", ["changed", "outside", "database"])
def test_changed_source_or_target_or_unapproved_scope_rejects_before_mutation(kind):
    adapter = Adapter()
    approved = plan(adapter)
    if kind == "changed":
        adapter.sources = (replace(source(), extraction_id=UUID(int=41)),)
    elif kind == "database":
        adapter.identity = replace(adapter.identity, database_oid="43")
    else:
        adapter.outside = True
    result = reconstruct(approved, adapter=adapter)
    assert result.status in {"SOURCE_CHANGED", "UNAPPROVED_SOURCE"}
    assert not adapter.calls


@pytest.mark.parametrize("state", ["MISSING", "PARTIAL", "FAILED", "RETRYABLE_FAILED"])
def test_preparation_noop_is_never_success(state):
    adapter = Adapter()
    adapter.progress = SourceProgress(state)
    adapter.prepare = lambda item: None
    result = reconstruct(plan(adapter), adapter=adapter)
    assert result.status == "PARTIAL"
    assert result.source_unavailable == 1
    assert "metadata_prepare" not in adapter.calls


def test_empty_or_unresolved_metadata_is_partial():
    adapter = Adapter()
    adapter.progress = SourceProgress("PREPARED", UUID(int=50), "PREPARED", unresolved_pages=2)
    result = reconstruct(plan(adapter), adapter=adapter)
    assert result.status == "PARTIAL" and result.unresolved_pages == 2


@pytest.mark.parametrize(
    "field", ["identity_unresolved", "semantic_unresolved", "pending_publications"]
)
def test_semantic_identity_or_publication_gaps_remain_explicit(field):
    adapter = Adapter()
    adapter.progress = SourceProgress(
        "PREPARED", UUID(int=50), "PREPARED", components=1, **{field: 1}
    )
    result = reconstruct(plan(adapter), adapter=adapter)
    assert result.status == "PARTIAL"
    assert getattr(result, field) == 1


def test_cancellation_and_budget_bound_mutation_steps():
    adapter = Adapter()
    assert (
        reconstruct(plan(adapter), adapter=adapter, stop_requested=lambda: True).status == "STOPPED"
    )
    assert not adapter.calls
    result = reconstruct(plan(adapter), adapter=adapter, max_steps=1)
    assert result.status == "BOUNDED" and adapter.calls == ["prepare"]


def test_rechecks_after_preparation_before_metadata():
    adapter = Adapter()
    original = adapter.prepare

    def changed(item):
        original(item)
        adapter.sources = (replace(source(), source_sha256="c" * 64),)

    adapter.prepare = changed
    assert reconstruct(plan(adapter), adapter=adapter).status == "SOURCE_CHANGED"
    assert adapter.calls == ["prepare"]


def test_adapter_error_is_sanitized():
    adapter = Adapter()

    def broken(item):
        raise RuntimeError("synthetic-sensitive-value")

    adapter.prepare = broken
    result = reconstruct(plan(adapter), adapter=adapter)
    assert result.status == "SOURCE_UNAVAILABLE"
    assert "synthetic-sensitive-value" not in repr(result)


def test_empty_sources_cannot_complete():
    adapter = Adapter()
    adapter.sources = ()
    with pytest.raises(TransitionError, match="PLAN_INVALID"):
        plan(adapter)


def test_plan_loader_preserves_identity_and_rejects_permissions_symlink_and_revision(tmp_path):
    import json
    from dataclasses import asdict

    from scripts.restructure_existing_documents import load_plan

    approved = plan(Adapter())
    path = tmp_path / "synthetic-plan.json"
    path.write_text(json.dumps(asdict(approved), default=str))
    path.chmod(0o600)
    assert load_plan(path) == approved
    path.chmod(0o644)
    with pytest.raises(TransitionError, match="PLAN_INVALID"):
        load_plan(path)
    path.chmod(0o600)
    link = tmp_path / "synthetic-link.json"
    link.symlink_to(path)
    with pytest.raises(TransitionError, match="PLAN_INVALID"):
        load_plan(link)
    value = asdict(approved)
    value["preparation_revision"] = "synthetic-old-revision"
    path.write_text(json.dumps(value, default=str))
    with pytest.raises(TransitionError, match="PLAN_INVALID"):
        load_plan(path)


def test_deadline_is_checked_before_first_mutation():
    adapter = Adapter()
    ticks = iter([0, 2])
    assert (
        reconstruct(
            plan(adapter), adapter=adapter, deadline_seconds=1, clock=lambda: next(ticks)
        ).status
        == "BOUNDED"
    )
    assert not adapter.calls


def test_metadata_runner_filters_exact_approved_generation(monkeypatch):
    import psycopg
    from familycare_worker.document_metadata_repository import DocumentMetadataRunner

    class Connection:
        queries = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, query, values=None):
            self.queries.append((query, values))
            return self

        def fetchone(self):
            return None

    connection = Connection()
    monkeypatch.setattr(psycopg, "connect", lambda *args, **kwargs: connection)
    selected = UUID(int=50)
    assert (
        DocumentMetadataRunner("postgresql://synthetic", generation_id=selected).run_once(
            "synthetic"
        )
        is False
    )
    query, values = connection.queries[-1]
    assert "g.id=%s" in query and values[-2:] == (selected, selected)
    assert DocumentMetadataRunner("postgresql://synthetic").run_once("synthetic") is False
    assert connection.queries[-1][1][-2:] == (None, None)


def test_plan_capture_uses_readonly_transaction_and_explicit_ids(monkeypatch):
    from scripts import restructure_existing_documents as module

    class Connection:
        queries = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, query):
            self.queries.append(query)

    connection = Connection()
    monkeypatch.setattr(module, "_connect", lambda url: connection)
    monkeypatch.setattr(module, "_database_identity", lambda conn: Adapter.identity)

    def snapshot(conn, ids):
        assert ids == (source().batch_item_id,)
        return (source(),)

    monkeypatch.setattr(module, "_snapshot", snapshot)
    assert module.capture_plan("postgresql://synthetic", [source().batch_item_id]) == plan(
        Adapter()
    )
    assert connection.queries == ["SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"]


def test_selected_verification_hashes_one_source_but_checks_full_scope(monkeypatch):
    from scripts import restructure_existing_documents as module

    approved = ReconstructionPlan(Adapter.identity, (source(), source(2)))
    hashed = []

    class Connection:
        queries = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, query, values=None):
            self.queries.append((query, values))
            return self

        def fetchone(self):
            return {"outside": False}

    connection = Connection()
    monkeypatch.setattr(module, "_connect", lambda url: connection)
    monkeypatch.setattr(module, "_database_identity", lambda conn: approved.database)

    def snapshot(conn, ids, checkpoint):
        hashed.append(tuple(ids))
        return tuple(item for item in approved.sources if item.batch_item_id in ids)

    monkeypatch.setattr(module, "_snapshot", snapshot)
    adapter = module.PostgresReconstructionAdapter("postgresql://synthetic")
    adapter.verify(approved, source=approved.sources[1])
    assert hashed == [(source(2).batch_item_id,)]
    assert connection.queries[-1][1] == ([source().batch_item_id, source(2).batch_item_id],) * 3
    adapter.verify(approved)
    assert hashed[-1] == (source().batch_item_id, source(2).batch_item_id)


@pytest.mark.parametrize("foreign", [source(3), replace(source(), source_sha256="c" * 64)])
def test_selected_verification_cannot_replace_approved_source(monkeypatch, foreign):
    from scripts import restructure_existing_documents as module

    def forbidden(url):
        raise AssertionError("No connection for an unapproved source")

    monkeypatch.setattr(module, "_connect", forbidden)
    adapter = module.PostgresReconstructionAdapter("postgresql://synthetic")
    with pytest.raises(TransitionError, match="UNAPPROVED_SOURCE"):
        adapter.verify(plan(Adapter()), source=foreign)


def test_other_source_change_is_detected_before_any_global_publication():
    class PerSourceAdapter(Adapter):
        def __init__(self):
            super().__init__()
            self.sources = (source(), source(2))
            self.states = {item.batch_item_id: SourceProgress("MISSING") for item in self.sources}
            self.checks = []

        def verify(self, approved, *, source=None):
            self.checks.append(source)
            expected = approved.sources if source is None else (source,)
            actual = (
                self.sources
                if source is None
                else tuple(
                    item for item in self.sources if item.batch_item_id == source.batch_item_id
                )
            )
            if expected != actual:
                raise TransitionError("SOURCE_CHANGED")

        def observe(self, item):
            return self.states[item.batch_item_id]

        def prepare(self, item):
            self.calls.append(("prepare", item.batch_item_id))
            self.states[item.batch_item_id] = SourceProgress(
                "PREPARED", UUID(int=item.batch_item_id.int + 50), "MISSING"
            )
            if item.batch_item_id == source(2).batch_item_id:
                self.sources = (replace(self.sources[0], source_sha256="c" * 64), self.sources[1])

        def metadata(self, generation):
            item_id = UUID(int=generation.int - 50)
            self.calls.append(("metadata_prepare", item_id))
            self.states[item_id] = SourceProgress("PREPARED", generation, "PREPARED", components=1)

    adapter = PerSourceAdapter()
    result = reconstruct(plan(adapter), adapter=adapter)
    assert result.status == "SOURCE_CHANGED"
    assert adapter.checks[0] is None and adapter.checks[-1] is None
    assert source() in adapter.checks and source(2) in adapter.checks
    assert adapter.calls == [
        ("prepare", source().batch_item_id),
        ("metadata_prepare", source().batch_item_id),
        ("prepare", source(2).batch_item_id),
        ("metadata_prepare", source(2).batch_item_id),
    ]


def test_bounded_run_keeps_already_observed_progress_without_extra_reads():
    adapter = Adapter()
    original = adapter.observe
    observed = []

    def observe(item):
        observed.append(item)
        return original(item)

    adapter.observe = observe
    result = reconstruct(plan(adapter), adapter=adapter, max_steps=2)
    assert result.status == "BOUNDED"
    assert result.prepared == 1
    assert len(observed) == 2  # Initial state and the existing post-preparation read.
