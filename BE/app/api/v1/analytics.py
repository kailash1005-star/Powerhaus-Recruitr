"""
Analytics API Endpoints
GET /api/v1/analytics/jobs       - Jobs analytics (with date range)
GET /api/v1/analytics/companies  - Companies analytics
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from app.database import get_database
from app.security.tenant import TenantContext, tenant_scope
from datetime import datetime, timedelta
from bson import ObjectId

router = APIRouter()


async def get_db():
    return await get_database()


def _date_filter(days: int | None):
    """Return a $match-compatible date filter dict, or empty dict for 'all'."""
    if days is None or days <= 0:
        return {}
    cutoff = datetime.utcnow() - timedelta(days=days)
    return {"createdAt": {"$gte": cutoff}}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# JOBS ANALYTICS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.get("/jobs")
async def jobs_analytics(
    days: int = Query(7, description="Date range: 7, 30, 90, or 0 for all time"),
    ctx: TenantContext = Depends(tenant_scope),
    db=Depends(get_db),
):
    try:
        jobs_col = db["jobs"]
        date_match = _date_filter(days if days > 0 else None)
        # Scope every aggregation stage below to the caller's tenant (admins: all).
        base_match = {"$match": ctx.read_filter(date_match)}

        # ── 1. Summary counts ────────────────────────────────────────────
        summary_pipeline = [
            base_match,
            {
                "$group": {
                    "_id": None,
                    "total": {"$sum": 1},
                    "accepted": {
                        "$sum": {"$cond": [{"$eq": ["$qualityStatus", "good"]}, 1, 0]}
                    },
                    "rejected": {
                        "$sum": {"$cond": [{"$eq": ["$qualityStatus", "poor"]}, 1, 0]}
                    },
                }
            },
        ]
        summary_cursor = jobs_col.aggregate(summary_pipeline)
        summary_raw = await summary_cursor.to_list(length=1)
        summary = summary_raw[0] if summary_raw else {"total": 0, "accepted": 0, "rejected": 0}
        summary.pop("_id", None)
        total = summary["total"]
        summary["acceptanceRate"] = round(
            (summary["accepted"] / total * 100) if total > 0 else 0, 1
        )

        # ── 2. By board ──────────────────────────────────────────────────
        board_pipeline = [
            base_match,
            {"$group": {"_id": "$boardName", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 10},
        ]
        by_board = [
            {"board": doc["_id"] or "unknown", "count": doc["count"]}
            async for doc in jobs_col.aggregate(board_pipeline)
        ]

        # ── 3. By quality status ─────────────────────────────────────────
        quality_pipeline = [
            base_match,
            {"$group": {"_id": "$qualityStatus", "count": {"$sum": 1}}},
        ]
        by_quality = [
            {"status": doc["_id"] or "unknown", "count": doc["count"]}
            async for doc in jobs_col.aggregate(quality_pipeline)
        ]

        # ── 4. Top rejection reasons ─────────────────────────────────────
        rejection_pipeline = [
            base_match,
            {"$match": {"rejectionReason": {"$ne": None}}},
            {"$group": {"_id": "$rejectionReason", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 10},
        ]
        by_rejection = [
            {"reason": doc["_id"], "count": doc["count"]}
            async for doc in jobs_col.aggregate(rejection_pipeline)
        ]

        # ── 5. By search keyword (title searched) ────────────────────────
        keyword_pipeline = [
            base_match,
            {"$group": {"_id": "$jobDetails.searchKeyword", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 15},
        ]
        by_keyword = [
            {"keyword": doc["_id"] or "unknown", "count": doc["count"]}
            async for doc in jobs_col.aggregate(keyword_pipeline)
        ]

        # ── 6. By location ───────────────────────────────────────────────
        location_pipeline = [
            base_match,
            {"$group": {"_id": "$location", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 15},
        ]
        by_location = [
            {"location": doc["_id"] or "unknown", "count": doc["count"]}
            async for doc in jobs_col.aggregate(location_pipeline)
        ]

        # ── 7. Daily trend ───────────────────────────────────────────────
        trend_pipeline = [
            base_match,
            {
                "$group": {
                    "_id": {
                        "$dateToString": {"format": "%Y-%m-%d", "date": "$createdAt"}
                    },
                    "total": {"$sum": 1},
                    "accepted": {
                        "$sum": {"$cond": [{"$eq": ["$qualityStatus", "good"]}, 1, 0]}
                    },
                    "rejected": {
                        "$sum": {"$cond": [{"$eq": ["$qualityStatus", "poor"]}, 1, 0]}
                    },
                }
            },
            {"$sort": {"_id": 1}},
        ]
        daily_trend = [
            {"date": doc["_id"], "total": doc["total"], "accepted": doc["accepted"], "rejected": doc["rejected"]}
            async for doc in jobs_col.aggregate(trend_pipeline)
        ]

        return {
            "days": days,
            "summary": summary,
            "byBoard": by_board,
            "byQuality": by_quality,
            "byRejectionReason": by_rejection,
            "byKeyword": by_keyword,
            "byLocation": by_location,
            "dailyTrend": daily_trend,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Jobs analytics error: {str(e)}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COMPANIES ANALYTICS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.get("/companies")
async def companies_analytics(
    ctx: TenantContext = Depends(tenant_scope),
    db=Depends(get_db),
):
    """Company analytics — scoped to the caller's tenant (admins: all)."""
    try:
        companies_col = db["companies"]

        # Companies are shared/deduped and carry no tenantId, so scope them to the
        # set this tenant's jobs reference. `pre` is prepended to every pipeline.
        pre: list = []
        if not ctx.is_admin:
            company_ids = [
                cid for cid in await db["jobs"].distinct("companyId", {"tenantId": ctx.tenant_id}) if cid
            ]
            pre = [{"$match": {"_id": {"$in": company_ids}}}]

        # ── 1. Summary ───────────────────────────────────────────────────
        summary_pipeline = pre + [
            {
                "$group": {
                    "_id": None,
                    "total": {"$sum": 1},
                    "accepted": {
                        "$sum": {"$cond": [{"$eq": ["$isEligible", True]}, 1, 0]}
                    },
                    "rejected": {
                        "$sum": {"$cond": [{"$eq": ["$isEligible", False]}, 1, 0]}
                    },
                    "avgEmployees": {"$avg": "$employeeCount"},
                }
            },
        ]
        summary_raw = await companies_col.aggregate(summary_pipeline).to_list(length=1)
        summary = summary_raw[0] if summary_raw else {"total": 0, "accepted": 0, "rejected": 0, "avgEmployees": 0}
        summary.pop("_id", None)
        total = summary["total"]
        summary["acceptanceRate"] = round(
            (summary["accepted"] / total * 100) if total > 0 else 0, 1
        )
        summary["avgEmployees"] = round(summary.get("avgEmployees") or 0)

        # ── 2. By eligibility ────────────────────────────────────────────
        elig_pipeline = pre + [
            {"$group": {"_id": "$isEligible", "count": {"$sum": 1}}},
        ]
        by_eligibility = [
            {
                "status": "accepted" if doc["_id"] is True else ("rejected" if doc["_id"] is False else "unknown"),
                "count": doc["count"],
            }
            async for doc in companies_col.aggregate(elig_pipeline)
        ]

        # ── 3. By industry ───────────────────────────────────────────────
        # industry field is comma-joined; split via $split
        industry_pipeline = pre + [
            {"$project": {"industries": {"$split": ["$industry", ", "]}}},
            {"$unwind": "$industries"},
            {"$group": {"_id": "$industries", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 15},
        ]
        by_industry = [
            {"industry": doc["_id"] or "unknown", "count": doc["count"]}
            async for doc in companies_col.aggregate(industry_pipeline)
        ]

        # ── 4. Top rejection reasons ─────────────────────────────────────
        rejection_pipeline = pre + [
            {"$match": {"isEligible": False, "notes": {"$ne": ""}}},
            {"$group": {"_id": "$notes", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 10},
        ]
        by_rejection = [
            {"reason": doc["_id"], "count": doc["count"]}
            async for doc in companies_col.aggregate(rejection_pipeline)
        ]

        # ── 5. By employee count range ───────────────────────────────────
        size_pipeline = pre + [
            {
                "$bucket": {
                    "groupBy": "$employeeCount",
                    "boundaries": [0, 51, 201, 1001, 5001, 100000],
                    "default": "100000+",
                    "output": {"count": {"$sum": 1}},
                }
            },
        ]
        size_labels = {0: "1-50", 51: "51-200", 201: "201-1K", 1001: "1K-5K", 5001: "5K+", "100000+": "100K+"}
        by_size = []
        async for doc in companies_col.aggregate(size_pipeline):
            label = size_labels.get(doc["_id"], str(doc["_id"]))
            by_size.append({"range": label, "count": doc["count"]})

        return {
            "summary": summary,
            "byEligibility": by_eligibility,
            "byIndustry": by_industry,
            "byRejectionReason": by_rejection,
            "bySize": by_size,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Companies analytics error: {str(e)}")
