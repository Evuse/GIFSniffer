import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, HttpUrl

BASE_DIR = Path(__file__).resolve().parent.parent
DOWNLOAD_DIR = BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(exist_ok=True)

app = FastAPI(title="GIFSniffer", version="1.0.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")


class MediaInfoRequest(BaseModel):
    url: HttpUrl


def _build_yt_dlp_base_cmd(url: str):
    cmd = ["yt-dlp", "-J", "--no-warnings", "--skip-download", url]

    cookies_path = os.getenv("INSTAGRAM_COOKIES_FILE")
    if cookies_path and Path(cookies_path).exists() and "instagram.com" in url:
        cmd.extend(["--cookies", cookies_path])

    username = os.getenv("INSTAGRAM_USERNAME")
    password = os.getenv("INSTAGRAM_PASSWORD")
    if username and password and "instagram.com" in url:
        cmd.extend(["--username", username, "--password", password])

    return cmd


def _ffmpeg_scale_filter(size: str) -> str:
    presets = {
        "original": "scale=iw:ih:flags=lanczos",
        "1080p": "scale=-2:1080:flags=lanczos",
        "720p": "scale=-2:720:flags=lanczos",
        "480p": "scale=-2:480:flags=lanczos",
        "360p": "scale=-2:360:flags=lanczos",
    }
    return presets.get(size, presets["original"])


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/api/info")
async def media_info(payload: MediaInfoRequest):
    cmd = _build_yt_dlp_base_cmd(str(payload.url))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise HTTPException(status_code=400, detail=f"Impossibile leggere il media: {proc.stderr}")

    import json

    data = json.loads(proc.stdout)
    formats = []
    for f in data.get("formats", []):
        if f.get("vcodec") == "none":
            continue
        formats.append(
            {
                "format_id": f.get("format_id"),
                "ext": f.get("ext"),
                "resolution": f.get("resolution") or f"{f.get('width')}x{f.get('height')}",
                "fps": f.get("fps"),
                "filesize": f.get("filesize") or f.get("filesize_approx"),
                "vcodec": f.get("vcodec"),
                "acodec": f.get("acodec"),
            }
        )

    return JSONResponse(
        {
            "title": data.get("title"),
            "duration": data.get("duration"),
            "uploader": data.get("uploader"),
            "formats": formats[:25],
            "note": "Per Instagram usa cookie/login per ridurre rate limit.",
        }
    )


@app.post("/api/download/video")
async def download_video(
    url: str = Form(...),
    format_id: str = Form("best"),
    size: str = Form("original"),
    crf: int = Form(23),
):
    token = str(uuid.uuid4())
    out_file = DOWNLOAD_DIR / f"{token}.mp4"

    tmp_dir = Path(tempfile.mkdtemp(prefix="gifsniffer_"))
    input_file = tmp_dir / "input.mp4"

    dl_cmd = ["yt-dlp", "-f", format_id, "-o", str(input_file), url]
    if "instagram.com" in url:
        cookies_path = os.getenv("INSTAGRAM_COOKIES_FILE")
        if cookies_path and Path(cookies_path).exists():
            dl_cmd.extend(["--cookies", cookies_path])

    proc = subprocess.run(dl_cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail=f"Download fallito: {proc.stderr}")

    vf = _ffmpeg_scale_filter(size)
    ff_cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_file),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-crf",
        str(crf),
        "-preset",
        "medium",
        "-pix_fmt",
        "yuv420p",
        str(out_file),
    ]
    ff = subprocess.run(ff_cmd, capture_output=True, text=True)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    if ff.returncode != 0:
        raise HTTPException(status_code=500, detail=f"Transcoding fallito: {ff.stderr}")

    return FileResponse(out_file, media_type="video/mp4", filename=f"video_{token}.mp4")


@app.post("/api/download/gif")
async def download_gif(
    url: str = Form(...),
    fps: int = Form(12),
    width: int = Form(480),
    colors: int = Form(128),
    speed: float = Form(1.0),
):
    token = str(uuid.uuid4())
    out_file = DOWNLOAD_DIR / f"{token}.gif"

    tmp_dir = Path(tempfile.mkdtemp(prefix="gifsniffer_"))
    input_file = tmp_dir / "input.mp4"
    palette_file = tmp_dir / "palette.png"

    dl_cmd = ["yt-dlp", "-f", "best", "-o", str(input_file), url]
    if "instagram.com" in url:
        cookies_path = os.getenv("INSTAGRAM_COOKIES_FILE")
        if cookies_path and Path(cookies_path).exists():
            dl_cmd.extend(["--cookies", cookies_path])

    proc = subprocess.run(dl_cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail=f"Download fallito: {proc.stderr}")

    base_filter = f"fps={fps},scale={width}:-1:flags=lanczos,setpts={1/speed}*PTS"
    pal_cmd = ["ffmpeg", "-y", "-i", str(input_file), "-vf", f"{base_filter},palettegen=max_colors={colors}", str(palette_file)]
    gif_cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_file),
        "-i",
        str(palette_file),
        "-lavfi",
        f"{base_filter}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5",
        str(out_file),
    ]

    pal = subprocess.run(pal_cmd, capture_output=True, text=True)
    if pal.returncode != 0:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Palette generation fallita: {pal.stderr}")

    gif = subprocess.run(gif_cmd, capture_output=True, text=True)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    if gif.returncode != 0:
        raise HTTPException(status_code=500, detail=f"Conversione GIF fallita: {gif.stderr}")

    return FileResponse(out_file, media_type="image/gif", filename=f"gif_{token}.gif")
