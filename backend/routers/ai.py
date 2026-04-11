"""AI router — Whisper subtitles + Pillow thumbnail. No API key required.
AI title/description generation is handled client-side via puter.js.
"""
import base64
import io
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from utils import TMP_DIR, find_video_path, run_ffmpeg

router = APIRouter(prefix="/ai")


class SubtitleBody(BaseModel):
    video_id: str
    burn_in: bool = True     # True = burn into video; False = return SRT only
    language: str = "auto"   # e.g. "en", "hi"; "auto" = detect


class ThumbnailBody(BaseModel):
    video_id: str
    text: str = ""
    at_sec: float = 1.0


@router.post("/subtitles")
def generate_subtitles(body: SubtitleBody):
    """Transcribe audio with faster-whisper; optionally burn subtitles into the video."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="faster-whisper not installed. Run: pip install faster-whisper",
        )

    src = find_video_path(body.video_id)

    # Extract audio for transcription
    audio_path = TMP_DIR / f"{body.video_id}_whisper.wav"
    run_ffmpeg(["-i", str(src), "-vn", "-ar", "16000", "-ac", "1", "-f", "wav", str(audio_path)])

    try:
        model = WhisperModel("small", device="cpu", compute_type="int8")
        lang = None if body.language == "auto" else body.language
        segments, info = model.transcribe(str(audio_path), language=lang)
        segments = list(segments)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Whisper transcription failed: {exc}") from exc
    finally:
        audio_path.unlink(missing_ok=True)

    # Build SRT
    def _fmt(t: float) -> str:
        h, rem = divmod(int(t), 3600)
        m, s = divmod(rem, 60)
        ms = int((t - int(t)) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    srt_lines = [
        f"{i}\n{_fmt(seg.start)} --> {_fmt(seg.end)}\n{seg.text.strip()}\n"
        for i, seg in enumerate(segments, 1)
    ]
    srt_text = "\n".join(srt_lines)

    srt_path = TMP_DIR / f"{body.video_id}.srt"
    srt_path.write_text(srt_text, encoding="utf-8")

    if not body.burn_in:
        return {"srt": srt_text, "language": info.language}

    # Burn subtitles into video
    out_id = str(uuid.uuid4())
    out_path = TMP_DIR / f"{out_id}.mp4"
    srt_escaped = str(srt_path).replace("\\", "/").replace(":", "\\:")
    run_ffmpeg(["-i", str(src), "-vf", f"subtitles='{srt_escaped}'", "-c:a", "copy", str(out_path)])

    return {"subtitled_video_id": out_id, "srt": srt_text, "language": info.language}


@router.post("/thumbnail")
def generate_thumbnail(body: ThumbnailBody):
    """Extract a frame and optionally overlay bold text to create a YouTube thumbnail."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="Pillow not installed. Run: pip install pillow",
        )

    src = find_video_path(body.video_id)
    frame_path = TMP_DIR / f"{body.video_id}_tn.jpg"

    # Extract frame at requested second, scaled to 1280x720
    run_ffmpeg([
        "-ss", str(body.at_sec), "-i", str(src), "-frames:v", "1",
        "-vf", "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720",
        str(frame_path),
    ])
    if not frame_path.is_file():
        raise HTTPException(status_code=500, detail="Frame extraction failed.")

    img = Image.open(str(frame_path)).convert("RGB")

    if body.text.strip():
        # Semi-transparent bottom banner
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        od.rectangle([(0, img.height - 130), (img.width, img.height)], fill=(0, 0, 0, 170))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("arial.ttf", 62)
        except Exception:
            font = ImageFont.load_default()

        text = body.text.strip()[:60]
        bbox = draw.textbbox((0, 0), text, font=font)
        tx = (img.width - (bbox[2] - bbox[0])) // 2
        ty = img.height - 115
        draw.text((tx + 2, ty + 2), text, font=font, fill=(0, 0, 0))    # shadow
        draw.text((tx, ty), text, font=font, fill=(255, 255, 255))        # white text

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    frame_path.unlink(missing_ok=True)
    return {"thumbnail_b64": b64}
