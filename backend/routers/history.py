"""History, disk stats, and presets router."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db import delete_history, delete_preset, get_history, get_presets, save_preset
from utils import TMP_DIR

router = APIRouter()


class PresetBody(BaseModel):
    name: str
    settings: dict


@router.get("/history")
def list_history(limit: int = 50, offset: int = 0):
    items = get_history(limit=limit, offset=offset)
    return {"items": items, "count": len(items)}


@router.delete("/history/{history_id}")
def remove_history(history_id: int):
    if not delete_history(history_id):
        raise HTTPException(status_code=404, detail="History entry not found.")
    return {"deleted": True}


@router.get("/stats/disk")
def disk_stats():
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    files = [p for p in TMP_DIR.iterdir() if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    return {
        "file_count": len(files),
        "total_bytes": total,
        "total_mb": round(total / (1024 * 1024), 2),
    }


@router.get("/presets")
def list_presets():
    return {"items": get_presets()}


@router.post("/presets")
def create_preset(body: PresetBody):
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Preset name cannot be empty.")
    save_preset(body.name.strip(), body.settings)
    return {"saved": True}


@router.delete("/presets/{preset_id}")
def remove_preset(preset_id: int):
    if not delete_preset(preset_id):
        raise HTTPException(status_code=404, detail="Preset not found.")
    return {"deleted": True}


class PipelinePresetBody(BaseModel):
    name: str
    settings: dict


@router.post("/presets/pipeline")
def create_pipeline_preset(body: PipelinePresetBody):
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Preset name cannot be empty.")
    # Store with a _type marker so we can distinguish pipeline presets
    settings = dict(body.settings)
    settings["_type"] = "pipeline"
    save_preset(body.name.strip(), settings)
    return {"saved": True}


@router.get("/presets/pipeline")
def list_pipeline_presets():
    all_presets = get_presets()
    pipeline = [p for p in all_presets if p["settings"].get("_type") == "pipeline"]
    return {"items": pipeline}


@router.delete("/presets/pipeline/{preset_id}")
def remove_pipeline_preset(preset_id: int):
    if not delete_preset(preset_id):
        raise HTTPException(status_code=404, detail="Preset not found.")
    return {"deleted": True}
