"""Evals for role-only pipeline creation.

A pipeline used to REQUIRE a company (companyId, or companyName + companyDomain
together) — company details are now optional, so a recruiter can start straight
from the role ("Senior SAP FI Consultant") with no company in hand, and the
company gets filled in later when a lead/campaign maps into the pipeline. These
pin the two things that make that safe:

  * a role-only pipeline is a genuinely valid, storable state (blank company
    fields, not a synthesized placeholder company);
  * MULTIPLE role-only pipelines can coexist — the company-collision check that
    protects against two pipelines for the same company must not fire when
    there's no company to collide on (blank companyDomain is not a shared
    identity).

The existing "full company info" path (companyId, or companyName+companyDomain
together) is re-verified unchanged: still deduped by company, still rejects a
lone name/domain as a likely typo rather than silently doing something odd
with it.

Offline: a tiny fake Mongo (find_one/insert_one over dicts) — no real DB.
"""
from __future__ import annotations

from typing import Any, Dict

import pytest
from bson import ObjectId
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from app.api.v1.pipelines import PipelineCreateSchema, create_pipeline
from app.security.tenant import TenantContext

# A concrete tenant scope for the direct-call tests (the API would inject this).
_CTX = TenantContext(tenant_id="castle", is_admin=False, sub="auth0|tester",
                     email="tester@castle-personal.de")


class _FakeCompanies:
    def __init__(self):
        self.docs: Dict[ObjectId, Dict[str, Any]] = {}

    async def find_one(self, filt):
        for d in self.docs.values():
            if all(d.get(k) == v for k, v in filt.items()):
                return d
        return None

    async def insert_one(self, doc):
        oid = ObjectId()
        self.docs[oid] = {**doc, "_id": oid}

        class _R:
            inserted_id = oid
        return _R()


class _FakePipelines:
    def __init__(self):
        self.docs: Dict[ObjectId, Dict[str, Any]] = {}

    async def find_one(self, filt):
        for d in self.docs.values():
            if "$or" in filt:
                if any(all(d.get(k) == v for k, v in clause.items()) for clause in filt["$or"]):
                    return d
            elif all(d.get(k) == v for k, v in filt.items()):
                return d
        return None

    async def insert_one(self, doc):
        # Mirrors the real partial unique index: only a non-empty companyDomain
        # is ever enforced as unique.
        domain = doc.get("companyDomain")
        if domain:
            for d in self.docs.values():
                if d.get("companyDomain") == domain:
                    raise DuplicateKeyError("dup companyDomain")
        oid = ObjectId()
        self.docs[oid] = {**doc, "_id": oid}

        class _R:
            inserted_id = oid
        return _R()


class _FakeDb:
    def __init__(self):
        self.companies = _FakeCompanies()
        self.pipelines = _FakePipelines()

    def __getitem__(self, name):
        return {"companies": self.companies, "candidatePipelines": self.pipelines}[name]


def _schema(**kw) -> PipelineCreateSchema:
    return PipelineCreateSchema(**kw)


class TestRoleOnlyPipeline:
    @pytest.mark.asyncio
    async def test_no_company_fields_succeeds_with_blank_company(self):
        db = _FakeDb()
        out = await create_pipeline(_schema(), db=db, ctx=_CTX)
        assert out["companyId"] is None
        assert out["companyName"] == ""
        assert out["companyDomain"] == ""
        assert out["source"] == "manual"

    @pytest.mark.asyncio
    async def test_multiple_role_only_pipelines_do_not_collide(self):
        """The company-collision guard must not fire between two pipelines that
        both have no company — that used to crash (None has no `_id`) or would
        wrongly treat one role-only pipeline as a duplicate of another."""
        db = _FakeDb()
        first = await create_pipeline(_schema(), db=db, ctx=_CTX)
        second = await create_pipeline(_schema(), db=db, ctx=_CTX)
        assert first["_id"] != second["_id"]
        assert len(db.pipelines.docs) == 2

    @pytest.mark.asyncio
    async def test_lone_company_field_is_rejected_as_likely_typo(self):
        db = _FakeDb()
        with pytest.raises(HTTPException) as exc:
            await create_pipeline(_schema(companyName="Acme Corp"), db=db, ctx=_CTX)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_lone_domain_is_also_rejected(self):
        db = _FakeDb()
        with pytest.raises(HTTPException) as exc:
            await create_pipeline(_schema(companyDomain="acme.com"), db=db, ctx=_CTX)
        assert exc.value.status_code == 400


class TestFullCompanyPipelineUnchanged:
    @pytest.mark.asyncio
    async def test_name_and_domain_together_creates_a_company(self):
        db = _FakeDb()
        out = await create_pipeline(
            _schema(companyName="Acme Corp", companyDomain="acme.com"), db=db, ctx=_CTX,
        )
        assert out["companyName"] == "Acme Corp"
        assert out["companyDomain"] == "acme.com"
        assert out["companyId"] is not None
        assert len(db.companies.docs) == 1

    @pytest.mark.asyncio
    async def test_same_domain_twice_is_still_a_duplicate(self):
        db = _FakeDb()
        await create_pipeline(_schema(companyName="Acme Corp", companyDomain="acme.com"), db=db, ctx=_CTX)
        with pytest.raises(HTTPException) as exc:
            await create_pipeline(_schema(companyName="Acme Corp Again", companyDomain="acme.com"), db=db, ctx=_CTX)
        assert exc.value.status_code == 409
        assert exc.value.detail["error"] == "pipeline_already_exists"

    @pytest.mark.asyncio
    async def test_role_only_then_real_company_do_not_collide(self):
        """A role-only pipeline followed by a fully-specified one for an
        unrelated company must both succeed — order must not matter."""
        db = _FakeDb()
        role_only = await create_pipeline(_schema(), db=db, ctx=_CTX)
        with_company = await create_pipeline(
            _schema(companyName="Acme Corp", companyDomain="acme.com"), db=db, ctx=_CTX,
        )
        assert role_only["_id"] != with_company["_id"]
        assert with_company["companyDomain"] == "acme.com"
