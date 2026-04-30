import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import requests
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, HttpUrl
from PIL import Image

BASE_DIR = Path(__file__).resolve().parent.parent
DOWNLOAD_DIR = BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(exist_ok=True)

app = FastAPI(title="GIFSniffer", version="1.1.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")


class MediaInfoRequest(BaseModel):
    url: HttpUrl
    instagram_username: str | None = None
    instagram_password: str | None = None
    instagram_cookies_file: str | None = None


def _build_yt_dlp_base_cmd(url: str, username: str | None = None, password: str | None = None, cookies_file: str | None = None):
    cmd = ["yt-dlp", "-J", "--no-warnings", "--skip-download", url]

    cookies_path = cookies_file or os.getenv("INSTAGRAM_COOKIES_FILE")
    if cookies_path and Path(cookies_path).exists() and "instagram.com" in url:
        cmd.extend(["--cookies", cookies_path])

    username = username or os.getenv("INSTAGRAM_USERNAME")
    password = password or os.getenv("INSTAGRAM_PASSWORD")
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


def _run_json_info(url: str, username: str | None = None, password: str | None = None, cookies_file: str | None = None) -> dict:
    cmd = _build_yt_dlp_base_cmd(url, username=username, password=password, cookies_file=cookies_file)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise HTTPException(status_code=400, detail=f"Impossibile leggere il media: {proc.stderr}")
    return json.loads(proc.stdout)


def _pick_best_image_url(data: dict) -> str | None:
    thumbs = data.get("thumbnails") or []
    if thumbs:
        thumbs = sorted(thumbs, key=lambda t: (t.get("height") or 0) * (t.get("width") or 0), reverse=True)
        return thumbs[0].get("url")
    return data.get("thumbnail")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/api/info")
async def media_info(payload: MediaInfoRequest):
    data = _run_json_info(str(payload.url), username=payload.instagram_username, password=payload.instagram_password, cookies_file=payload.instagram_cookies_file)
    formats = []
    for f in data.get("formats", []):
        if f.get("vcodec") == "none" and f.get("acodec") == "none":
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

    image_url = _pick_best_image_url(data)

    return JSONResponse(
        {
            "title": data.get("title"),
            "duration": data.get("duration"),
            "uploader": data.get("uploader"),
            "formats": formats[:25],
            "image_candidate": image_url,
            "note": "Supporto video, immagini e GIF. Per Instagram usa cookie/login per ridurre rate limit.",
        }
    )


@app.post("/api/download/video")
async def download_video(url: str = Form(...), format_id: str = Form("best"), size: str = Form("original"), crf: int = Form(23), instagram_username: str = Form(""), instagram_password: str = Form(""), instagram_cookies_file: str = Form("")):
    token = str(uuid.uuid4())
    out_file = DOWNLOAD_DIR / f"{token}.mp4"
    tmp_dir = Path(tempfile.mkdtemp(prefix="gifsniffer_"))
    input_file = tmp_dir / "input.mp4"
    dl_cmd = ["yt-dlp", "-f", format_id, "-o", str(input_file), url]
    dl_cmd = _build_yt_dlp_base_cmd(url, instagram_username or None, instagram_password or None, instagram_cookies_file or None)
    dl_cmd[1:1] = ["-f", format_id, "-o", str(input_file)]
    proc = subprocess.run(dl_cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail=f"Download fallito: {proc.stderr}")

    ff = subprocess.run(["ffmpeg", "-y", "-i", str(input_file), "-vf", _ffmpeg_scale_filter(size), "-c:v", "libx264", "-crf", str(crf), "-preset", "medium", "-pix_fmt", "yuv420p", str(out_file)], capture_output=True, text=True)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    if ff.returncode != 0:
        raise HTTPException(status_code=500, detail=f"Transcoding fallito: {ff.stderr}")
    return FileResponse(out_file, media_type="video/mp4", filename=f"video_{token}.mp4")


@app.post("/api/download/gif")
async def download_gif(url: str = Form(...), fps: int = Form(12), width: int = Form(480), colors: int = Form(128), speed: float = Form(1.0), instagram_username: str = Form(""), instagram_password: str = Form(""), instagram_cookies_file: str = Form("")):
    token = str(uuid.uuid4())
    out_file = DOWNLOAD_DIR / f"{token}.gif"
    tmp_dir = Path(tempfile.mkdtemp(prefix="gifsniffer_"))
    input_file = tmp_dir / "input.mp4"
    palette_file = tmp_dir / "palette.png"

    dl_cmd = _build_yt_dlp_base_cmd(url, instagram_username or None, instagram_password or None, instagram_cookies_file or None)
    dl_cmd[1:1] = ["-f", "best", "-o", str(input_file)]
    proc = subprocess.run(dl_cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail=f"Download fallito: {proc.stderr}")

    base_filter = f"fps={fps},scale={width}:-1:flags=lanczos,setpts={1/speed}*PTS"
    pal = subprocess.run(["ffmpeg", "-y", "-i", str(input_file), "-vf", f"{base_filter},palettegen=max_colors={colors}", str(palette_file)], capture_output=True, text=True)
    if pal.returncode != 0:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Palette generation fallita: {pal.stderr}")

    gif = subprocess.run(["ffmpeg", "-y", "-i", str(input_file), "-i", str(palette_file), "-lavfi", f"{base_filter}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5", str(out_file)], capture_output=True, text=True)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    if gif.returncode != 0:
        raise HTTPException(status_code=500, detail=f"Conversione GIF fallita: {gif.stderr}")

    return FileResponse(out_file, media_type="image/gif", filename=f"gif_{token}.gif")


@app.post("/api/download/image")
async def download_image(url: str = Form(...), width: int = Form(0), quality: int = Form(90), output_format: str = Form("jpg"), instagram_username: str = Form(""), instagram_password: str = Form(""), instagram_cookies_file: str = Form("")):
    data = _run_json_info(url, username=instagram_username or None, password=instagram_password or None, cookies_file=instagram_cookies_file or None)
    img_url = _pick_best_image_url(data)
    if not img_url:
        raise HTTPException(status_code=400, detail="Nessuna immagine trovata nella URL.")

    r = requests.get(img_url, timeout=60)
    if r.status_code != 200:
        raise HTTPException(status_code=400, detail="Impossibile scaricare l'immagine sorgente")

    token = str(uuid.uuid4())
    ext = "jpg" if output_format.lower() not in {"jpg", "png", "webp"} else output_format.lower()
    out_file = DOWNLOAD_DIR / f"image_{token}.{ext}"

    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as tmp:
        tmp.write(r.content)
        tmp_path = Path(tmp.name)

    img = Image.open(tmp_path).convert("RGB")
    tmp_path.unlink(missing_ok=True)

    if width and width > 0:
        h = int((width / img.width) * img.height)
        img = img.resize((width, h), Image.Resampling.LANCZOS)

    save_kwargs = {}
    if ext in {"jpg", "webp"}:
        save_kwargs["quality"] = max(35, min(100, quality))
    img.save(out_file, format="JPEG" if ext == "jpg" else ext.upper(), **save_kwargs)

    mtype = "image/jpeg" if ext == "jpg" else ("image/png" if ext == "png" else "image/webp")
    return FileResponse(out_file, media_type=mtype, filename=out_file.name)
