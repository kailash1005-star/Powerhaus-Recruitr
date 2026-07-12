"""
Cost Analyser API — reads the metered spend ledger.

  GET  /cost/overview?range=30d              — totals, by-service, by-stage, trend
  GET  /cost/group/{stage}?range=30d         — all line items in a stage
  GET  /cost/search/{group_key}              — one search: itemized events
  GET  /cost/price-book                      — price book (Settings → Costs)
  PATCH /cost/price-book                     — edit a rate / subscription

Aggregation lives in ``cost_service``; these are thin wrappers with validation.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services import cost_service as cost

router = APIRouter()

_RANGES = {"7d", "14d", "30d", "90d", "all"}
_STAGES = {
    cost.STAGE_JOB, cost.STAGE_CANDIDATE, cost.STAGE_MATCHING,
    cost.STAGE_OUTREACH, cost.STAGE_COMPANY,
}


def _range(range_key: str) -> str:
    if range_key not in _RANGES:
        raise HTTPException(400, f"range must be one of {sorted(_RANGES)}")
    return range_key


@router.get("/overview")
async def cost_overview(range: str = Query("30d")):
    try:
        return await cost.overview(_range(range))
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Error building cost overview: {e}")


@router.get("/group/{stage}")
async def cost_group(stage: str, range: str = Query("30d")):
    if stage not in _STAGES:
        raise HTTPException(400, f"stage must be one of {sorted(_STAGES)}")
    try:
        return await cost.group(stage, _range(range))
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Error building cost group: {e}")


@router.get("/search/{group_key:path}")
async def cost_search(group_key: str):
    try:
        return await cost.line_item(group_key)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Error building cost line item: {e}")


@router.get("/price-book")
async def get_price_book():
    try:
        return {"items": await cost.list_price_book()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Error reading price book: {e}")


class PriceEditSchema(BaseModel):
    service: str
    model: Optional[str] = None
    monthlyUsd: Optional[float] = None
    usdPerCredit: Optional[float] = None
    includedCredits: Optional[float] = None
    inUsdPer1M: Optional[float] = None
    outUsdPer1M: Optional[float] = None
    usdPerUnit: Optional[float] = None
    allocateBy: Optional[str] = None


@router.patch("/price-book")
async def edit_price_book(body: PriceEditSchema):
    patch = body.model_dump(exclude_none=True, exclude={"service", "model"})
    try:
        return await cost.update_price_entry(body.service, body.model, patch)
    except ValueError as ve:
        raise HTTPException(404, str(ve))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Error updating price book: {e}")
