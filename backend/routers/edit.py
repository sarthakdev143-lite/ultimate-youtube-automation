"""Edit router — advanced ffmpeg editing + audio upload."""
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from utils import (
    TMP_DIR, cleanup_old_tmp_files, escape_drawtext,
    find_video_path, hex_to_ffmpeg_color, overlay_xy, run_ffmpeg,
)

router = APIRouter()

ALLOWED_AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac"}
VALID_POSITIONS = {"top", "bottom", "center", "top-left", "top-right", "bottom-left", "bottom-right"}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class TextOverlay(BaseModel):
    text: str
    position: str
    font_size: int = Field(ge=8, le=200)
    color: str
    start_sec: float = Field(ge=0)
    end_sec: float = Field(ge=0)


class TrimSpec(BaseModel):
    start_sec: float = Field(ge=0)
    end_sec: float = Field(ge=0)


class ColorGrade(BaseModel):
    brightness: float = Field(default=1.0, ge=0.0, le=3.0)
    contrast: float = Field(default=1.0, ge=0.0, le=3.0)
    saturation: float = Field(default=1.0, ge=0.0, le=3.0)


class EditBody(BaseModel):
    video_id: str
    # Existing features
    text_overlays: list[TextOverlay] = Field(default_factory=list)
    trim: TrimSpec | None = None
    color_grade: ColorGrade | None = None
    speed: float = Field(default=1.0, ge=0.25, le=4.0)
    mute_audio: bool = False
    fade_in_sec: float = Field(default=0.0, ge=0.0)
    fade_out_sec: float = Field(default=0.0, ge=0.0)
    watermark_text: str = ""
    # New features
    rotate: int = Field(default=0)      # 0 | 90 | 180 | 270
    flip_h: bool = False
    flip_v: bool = False
    crop_9_16: bool = False             # crop center to 9:16
    auto_resize: bool = False           # scale+pad to 1080×1920
    remove_silence: bool = False
    music_audio_id: str = ""            # ID returned by /upload-audio


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/upload-audio")
async def upload_audio(file: UploadFile = File(...)):
    """Accept a background music file and return an audio_id."""
    cleanup_old_tmp_files()
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_AUDIO_EXTS:
        raise HTTPException(status_code=400, detail=f"Unsupported audio format '{suffix}'. Use: {', '.join(ALLOWED_AUDIO_EXTS)}")
    audio_id = str(uuid.uuid4())
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    audio_path = TMP_DIR / f"{audio_id}{suffix}"
    audio_path.write_bytes(await file.read())
    return {"audio_id": audio_id}


@router.get("/video/{video_id}/file")
def stream_video(video_id: str):
    """Stream a video file for in-browser preview."""
    path = find_video_path(video_id)
    return FileResponse(str(path), media_type="video/mp4", filename=path.name)


@router.post("/edit")
def edit_video(body: EditBody):
    cleanup_old_tmp_files()
    src = find_video_path(body.video_id)

    # Validate
    if body.trim and body.trim.end_sec <= body.trim.start_sec:
        raise HTTPException(status_code=400, detail="Trim end must be greater than start.")
    if body.rotate not in (0, 90, 180, 270):
        raise HTTPException(status_code=400, detail="rotate must be 0, 90, 180, or 270.")
    for ov in body.text_overlays:
        if not ov.text.strip():
            raise HTTPException(status_code=400, detail="Overlay text cannot be empty.")
        if ov.end_sec <= ov.start_sec:
            raise HTTPException(status_code=400, detail="Overlay end must be greater than start.")
        if ov.position not in VALID_POSITIONS:
            raise HTTPException(status_code=400, detail=f"Invalid position: {ov.position}")

    trim = body.trim
    has_color = body.color_grade is not None
    has_speed = abs(body.speed - 1.0) > 0.01
    has_music = bool(body.music_audio_id.strip())

    has_any = any([
        body.text_overlays, trim, has_color, has_speed, body.mute_audio,
        body.fade_in_sec > 0, body.fade_out_sec > 0, bool(body.watermark_text.strip()),
        body.rotate != 0, body.flip_h, body.flip_v,
        body.crop_9_16, body.auto_resize, body.remove_silence, has_music,
    ])
    if not has_any:
        raise HTTPException(status_code=400, detail="Provide at least one edit operation.")

    edited_id = str(uuid.uuid4())
    out_path = TMP_DIR / f"{edited_id}.mp4"

    vf = _build_vf(body, trim)
    af = _build_af(body, trim)

    if has_music:
        _run_with_music(body, src, out_path, vf, trim)
    else:
        cmd: list[str] = []
        if trim:
            cmd.extend(["-ss", str(trim.start_sec), "-to", str(trim.end_sec)])
        cmd.extend(["-i", str(src)])
        if body.mute_audio:
            cmd.append("-an")
        else:
            if af:
                cmd.extend(["-af", ",".join(af)])
            cmd.extend(["-c:a", "aac"])
        if vf:
            cmd.extend(["-vf", ",".join(vf)])
        cmd.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p", str(out_path)])
        run_ffmpeg(cmd)

    if not out_path.is_file():
        raise HTTPException(status_code=500, detail="Edited file was not created.")
    return {"edited_video_id": edited_id}


# ---------------------------------------------------------------------------
# Filter chain builders
# ---------------------------------------------------------------------------

def _build_vf(body: EditBody, trim: TrimSpec | None) -> list[str]:
    vf: list[str] = []
    cg = body.color_grade

    if cg:
        vf.append(f"eq=brightness={cg.brightness - 1.0:.4f}:contrast={cg.contrast:.4f}:saturation={cg.saturation:.4f}")

    if abs(body.speed - 1.0) > 0.01:
        vf.append(f"setpts={1.0 / body.speed:.6f}*PTS")

    if body.fade_in_sec > 0:
        vf.append(f"fade=t=in:st=0:d={body.fade_in_sec:.2f}")
    if body.fade_out_sec > 0 and trim:
        st = (trim.end_sec - trim.start_sec) - body.fade_out_sec
        if st > 0:
            vf.append(f"fade=t=out:st={st:.2f}:d={body.fade_out_sec:.2f}")

    if body.rotate == 90:
        vf.append("transpose=1")
    elif body.rotate == 180:
        vf.append("transpose=1,transpose=1")
    elif body.rotate == 270:
        vf.append("transpose=2")

    if body.flip_h:
        vf.append("hflip")
    if body.flip_v:
        vf.append("vflip")

    if body.crop_9_16:
        vf.append("crop=ih*9/16:ih:(iw-ih*9/16)/2:0")

    if body.auto_resize:
        vf.append("scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2")

    if body.remove_silence:
        vf.append("silenceremove=stop_periods=-1:stop_duration=0.3:stop_threshold=-50dB")

    for ov in body.text_overlays:
        fc = hex_to_ffmpeg_color(ov.color)
        xy = overlay_xy(ov.position)
        esc = escape_drawtext(ov.text.strip())
        enable = f"between(t\\,{ov.start_sec}\\,{ov.end_sec})"
        vf.append(f"drawtext=text='{esc}':fontsize={ov.font_size}:fontcolor={fc}:{xy}:enable='{enable}'")

    if body.watermark_text.strip():
        esc_wm = escape_drawtext(body.watermark_text.strip())
        vf.append(f"drawtext=text='{esc_wm}':fontsize=28:fontcolor=0xFFFFFFAA:x=w-text_w-20:y=h-text_h-20")

    return vf


def _build_af(body: EditBody, trim: TrimSpec | None) -> list[str]:
    af: list[str] = []
    speed = body.speed

    if abs(speed - 1.0) > 0.01:
        if speed < 0.5:
            af += ["atempo=0.5", f"atempo={speed / 0.5:.4f}"]
        elif speed > 2.0:
            af += ["atempo=2.0", f"atempo={speed / 2.0:.4f}"]
        else:
            af.append(f"atempo={speed:.4f}")

    if body.fade_in_sec > 0:
        af.append(f"afade=t=in:st=0:d={body.fade_in_sec:.2f}")
    if body.fade_out_sec > 0 and trim:
        st = (trim.end_sec - trim.start_sec) - body.fade_out_sec
        if st > 0:
            af.append(f"afade=t=out:st={st:.2f}:d={body.fade_out_sec:.2f}")

    return af


def _run_with_music(body: EditBody, src: Path, out_path: Path, vf: list[str], trim: TrimSpec | None) -> None:
    music_matches = [
        p for p in TMP_DIR.glob(f"{body.music_audio_id}.*")
        if p.suffix.lower() not in (".jpg", ".jpeg", ".png")
    ]
    if not music_matches:
        raise HTTPException(status_code=404, detail="Audio file not found. Upload it again.")
    music = music_matches[0]

    cmd: list[str] = []
    if trim:
        cmd.extend(["-ss", str(trim.start_sec), "-to", str(trim.end_sec)])
    cmd.extend(["-i", str(src), "-i", str(music)])

    if vf:
        cmd.extend(["-vf", ",".join(vf)])

    if body.mute_audio:
        cmd.extend([
            "-filter_complex", "[1:a]aloop=loop=-1:size=2e9[bg]",
            "-map", "0:v", "-map", "[bg]",
        ])
    else:
        cmd.extend([
            "-filter_complex",
            "[0:a]volume=0.7[orig];[1:a]aloop=loop=-1:size=2e9,volume=0.35[bg];[orig][bg]amix=inputs=2[aout]",
            "-map", "0:v", "-map", "[aout]",
        ])

    cmd.extend(["-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p", "-shortest", str(out_path)])
    run_ffmpeg(cmd)
