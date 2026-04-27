import os
import re
import json
import uuid
import subprocess
import tempfile
import threading
from datetime import datetime

import fitz  # PyMuPDF
import numpy as np
import soundfile as sf
import requests as http_requests
from flask import Flask, render_template, request, jsonify, send_from_directory
from kokoro_onnx import Kokoro

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AUDIO_DIR = os.path.join(BASE_DIR, "audio")
MODELS_DIR = os.path.join(BASE_DIR, "models")
ZOTERO_DIR = os.path.expanduser(
    "~/Documents/Documents-it/Obsidian-Notes/Zotero/storage"
)
os.makedirs(AUDIO_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Model Management
# ---------------------------------------------------------------------------

def _ensure_models():
    """Download Kokoro model and voices from Hugging Face if missing."""
    os.makedirs(MODELS_DIR, exist_ok=True)
    files = {
        "kokoro-v1.0.onnx": "https://huggingface.co/hexgrad/Kokoro-82M/resolve/main/kokoro-v1.0.onnx",
        "voices-v1.0.bin": "https://huggingface.co/hexgrad/Kokoro-82M/resolve/main/voices-v1.0.bin"
    }
    
    for filename, url in files.items():
        path = os.path.join(MODELS_DIR, filename)
        if not os.path.exists(path):
            print(f"--- Downloading {filename} from Hugging Face...")
            try:
                with http_requests.get(url, stream=True, timeout=300) as r:
                    r.raise_for_status()
                    with open(path, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            f.write(chunk)
                print(f"--- Successfully downloaded {filename}")
            except Exception as e:
                print(f"--- Error downloading {filename}: {e}")
                if os.path.exists(path):
                    os.unlink(path)

# Download if needed
_ensure_models()

# Load Kokoro model if available
kokoro = None
model_path = os.path.join(MODELS_DIR, "kokoro-v1.0.onnx")
voices_path = os.path.join(MODELS_DIR, "voices-v1.0.bin")

if os.path.exists(model_path) and os.path.exists(voices_path):
    try:
        kokoro = Kokoro(model_path, voices_path)
        print("--- Kokoro TTS initialized successfully")
    except Exception as e:
        print(f"--- Failed to initialize Kokoro: {e}")
else:
    print("--- Kokoro models not found. TTS conversion will be disabled (Playback only mode)")

# Kokoro voice catalog
KOKORO_VOICES = {
    # American English
    "af_heart": "Heart (F)",
    "af_alloy": "Alloy (F)",
    "af_aoede": "Aoede (F)",
    "af_bella": "Bella (F)",
    "af_jessica": "Jessica (F)",
    "af_kore": "Kore (F)",
    "af_nicole": "Nicole (F)",
    "af_nova": "Nova (F)",
    "af_river": "River (F)",
    "af_sarah": "Sarah (F)",
    "af_sky": "Sky (F)",
    "am_adam": "Adam (M)",
    "am_echo": "Echo (M)",
    "am_eric": "Eric (M)",
    "am_fenrir": "Fenrir (M)",
    "am_liam": "Liam (M)",
    "am_michael": "Michael (M)",
    "am_onyx": "Onyx (M)",
    "am_puck": "Puck (M)",
    # British English
    "bf_alice": "Alice (F, British)",
    "bf_emma": "Emma (F, British)",
    "bf_isabella": "Isabella (F, British)",
    "bf_lily": "Lily (F, British)",
    "bm_daniel": "Daniel (M, British)",
    "bm_fable": "Fable (M, British)",
    "bm_george": "George (M, British)",
    "bm_lewis": "Lewis (M, British)",
}

# In-memory job tracking
jobs = {}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/convert", methods=["POST"])
def convert():
    data = request.json
    url = data.get("url", "").strip()
    local_path = data.get("local_path", "").strip()
    voice = data.get("voice", "af_heart")

    if not url and not local_path:
        return jsonify({"error": "URL or local_path is required"}), 400

    # Security: only allow local paths inside ZOTERO_DIR
    if local_path:
        real = os.path.realpath(local_path)
        if not real.startswith(os.path.realpath(ZOTERO_DIR)):
            return jsonify({"error": "Path not allowed"}), 403
        if not os.path.isfile(real):
            return jsonify({"error": "File not found"}), 404

    job_id = uuid.uuid4().hex[:8]
    jobs[job_id] = {
        "id": job_id,
        "status": "reading" if local_path else "downloading",
        "progress": 0,
        "error": None,
    }

    thread = threading.Thread(
        target=_process_conversion,
        args=(job_id, url or None, voice),
        kwargs={"local_path": local_path or None},
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/api/zotero")
def zotero_papers():
    """List all PDFs in Zotero storage."""
    if not os.path.isdir(ZOTERO_DIR):
        return jsonify([])

    papers = []
    for folder in os.listdir(ZOTERO_DIR):
        folder_path = os.path.join(ZOTERO_DIR, folder)
        if not os.path.isdir(folder_path):
            continue
        for fname in os.listdir(folder_path):
            if fname.lower().endswith(".pdf"):
                full = os.path.join(folder_path, fname)
                display = fname.replace(".pdf", "").replace("_", " ")
                papers.append({
                    "path": full,
                    "name": display,
                    "size": os.path.getsize(full),
                    "folder": folder,
                })
    papers.sort(key=lambda p: p["name"].lower())
    return jsonify(papers)


@app.route("/api/status/<job_id>")
def status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


@app.route("/api/library")
def library():
    tracks = []
    for fname in os.listdir(AUDIO_DIR):
        if fname.endswith(".json"):
            with open(os.path.join(AUDIO_DIR, fname)) as f:
                tracks.append(json.load(f))
    tracks.sort(key=lambda t: t.get("created_at", ""), reverse=True)
    return jsonify(tracks)


@app.route("/api/audio/<filename>")
def serve_audio(filename):
    return send_from_directory(AUDIO_DIR, filename)


@app.route("/api/track/<track_id>", methods=["DELETE"])
def delete_track(track_id):
    for ext in (".mp3", ".json"):
        path = os.path.join(AUDIO_DIR, track_id + ext)
        if os.path.exists(path):
            os.unlink(path)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------

batch_state = {
    "running": False,
    "total": 0,
    "done": 0,
    "current": None,
    "errors": [],
    "completed": [],
}


@app.route("/api/batch", methods=["POST"])
def batch_start():
    if batch_state["running"]:
        return jsonify({"error": "Batch already running"}), 409

    data = request.json or {}
    voice = data.get("voice", "af_heart")

    # Get all zotero papers, deduplicate by filename
    if not os.path.isdir(ZOTERO_DIR):
        return jsonify({"error": "Zotero directory not found"}), 404

    papers = []
    seen_names = set()
    for folder in os.listdir(ZOTERO_DIR):
        folder_path = os.path.join(ZOTERO_DIR, folder)
        if not os.path.isdir(folder_path):
            continue
        for fname in os.listdir(folder_path):
            if fname.lower().endswith(".pdf"):
                # Deduplicate by filename
                if fname in seen_names:
                    continue
                seen_names.add(fname)
                papers.append(os.path.join(folder_path, fname))

    # Skip papers already converted
    existing_sources = set()
    for f in os.listdir(AUDIO_DIR):
        if f.endswith(".json"):
            with open(os.path.join(AUDIO_DIR, f)) as fh:
                meta = json.load(fh)
                existing_sources.add(meta.get("source", ""))

    papers = [p for p in papers if p not in existing_sources]
    papers.sort()

    if not papers:
        return jsonify({"error": "All papers already converted"}), 200

    batch_state["running"] = True
    batch_state["total"] = len(papers)
    batch_state["done"] = 0
    batch_state["current"] = None
    batch_state["errors"] = []
    batch_state["completed"] = []

    thread = threading.Thread(
        target=_run_batch, args=(papers, voice), daemon=True
    )
    thread.start()

    return jsonify({"started": True, "total": len(papers)})


@app.route("/api/batch/status")
def batch_status():
    return jsonify(batch_state)


@app.route("/api/batch/stop", methods=["POST"])
def batch_stop():
    batch_state["running"] = False
    return jsonify({"ok": True})


def _run_batch(papers, voice):
    for i, pdf_path in enumerate(papers):
        if not batch_state["running"]:
            break

        name = os.path.basename(pdf_path).replace(".pdf", "")
        batch_state["current"] = name
        batch_state["done"] = i

        job_id = uuid.uuid4().hex[:8]
        jobs[job_id] = {
            "id": job_id,
            "status": "reading",
            "progress": 0,
            "error": None,
        }

        try:
            _process_conversion(job_id, None, voice, local_path=pdf_path)
            if jobs[job_id]["status"] == "error":
                batch_state["errors"].append(
                    {"name": name, "error": jobs[job_id]["error"]}
                )
            else:
                batch_state["completed"].append(name)
        except Exception as e:
            batch_state["errors"].append({"name": name, "error": str(e)})

    batch_state["done"] = batch_state["total"] if batch_state["running"] else i
    batch_state["running"] = False
    batch_state["current"] = None


@app.route("/api/voices")
def voices():
    voice_list = []
    for vid, label in KOKORO_VOICES.items():
        voice_list.append({"id": vid, "name": label, "engine": "kokoro"})
    voice_list.sort(key=lambda v: v["name"])
    return jsonify(voice_list)


# ---------------------------------------------------------------------------
# Background conversion
# ---------------------------------------------------------------------------

def _synthesize_kokoro(text, voice, job_id):
    """Synthesize text with Kokoro. Returns path to WAV file."""
    lang = "en-gb" if voice.startswith("b") else "en-us"

    # Split into chunks at paragraph boundaries to show progress
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [text]

    if not kokoro:
        raise RuntimeError("Kokoro engine is not initialized. Models may be missing.")

    chunks = []
    total = len(paragraphs)
    for i, para in enumerate(paragraphs):
        if not para.strip():
            continue
        pct = 50 + int((i / total) * 35)  # progress 50-85%
        jobs[job_id]["progress"] = pct

        samples, sr = kokoro.create(para, voice=voice, speed=1.0, lang=lang)
        chunks.append(samples)
        # Small silence between paragraphs
        chunks.append(np.zeros(int(sr * 0.4), dtype=samples.dtype))

    combined = np.concatenate(chunks)
    wav_path = os.path.join(AUDIO_DIR, f"{job_id}.wav")
    sf.write(wav_path, combined, sr)
    return wav_path


def _process_conversion(job_id, url, voice, local_path=None):
    try:
        if local_path:
            jobs[job_id]["status"] = "reading"
            jobs[job_id]["progress"] = 20
            pdf_path = local_path
            source_label = os.path.basename(local_path)
            cleanup_pdf = False
        else:
            jobs[job_id]["status"] = "downloading"
            jobs[job_id]["progress"] = 10

            resp = http_requests.get(
                url, timeout=120, headers={"User-Agent": "Mozilla/5.0"}, stream=True
            )
            resp.raise_for_status()

            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                for chunk in resp.iter_content(8192):
                    f.write(chunk)
                pdf_path = f.name
            source_label = url
            cleanup_pdf = True

        # Extract text
        jobs[job_id]["status"] = "extracting"
        jobs[job_id]["progress"] = 30

        doc = fitz.open(pdf_path)
        pages = doc.page_count
        raw_title, text = _extract_body_text(doc)
        doc.close()
        if cleanup_pdf:
            os.unlink(pdf_path)

        title = raw_title or _extract_title(text, source_label)
        text = _clean_text(text)
        word_count = len(text.split())

        if not text.strip():
            raise ValueError("No text could be extracted from the PDF")

        # Synthesize with Kokoro
        jobs[job_id]["status"] = "synthesizing"
        jobs[job_id]["progress"] = 50

        wav_path = _synthesize_kokoro(text, voice, job_id)

        # Encode to MP3
        jobs[job_id]["status"] = "encoding"
        jobs[job_id]["progress"] = 88

        mp3_path = os.path.join(AUDIO_DIR, f"{job_id}.mp3")
        subprocess.run(
            [
                "ffmpeg",
                "-i", wav_path,
                "-codec:a", "libmp3lame",
                "-b:a", "192k",
                "-y",
                mp3_path,
            ],
            check=True,
            capture_output=True,
            timeout=600,
        )
        os.unlink(wav_path)

        # Get duration
        probe = subprocess.run(
            [
                "ffprobe",
                "-i", mp3_path,
                "-show_entries", "format=duration",
                "-v", "quiet",
                "-of", "csv=p=0",
            ],
            capture_output=True,
            text=True,
        )
        duration = float(probe.stdout.strip()) if probe.stdout.strip() else 0
        file_size = os.path.getsize(mp3_path)

        # Save metadata
        meta = {
            "id": job_id,
            "title": title,
            "url": url or "",
            "source": local_path or url,
            "filename": f"{job_id}.mp3",
            "created_at": datetime.now().isoformat(),
            "duration_seconds": round(duration, 1),
            "voice": voice,
            "voice_name": KOKORO_VOICES.get(voice, voice),
            "engine": "kokoro",
            "pages": pages,
            "word_count": word_count,
            "file_size": file_size,
        }

        with open(os.path.join(AUDIO_DIR, f"{job_id}.json"), "w") as f:
            json.dump(meta, f, indent=2)

        jobs[job_id]["status"] = "complete"
        jobs[job_id]["progress"] = 100
        jobs[job_id]["metadata"] = meta

    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)


def _extract_body_text(doc):
    """Extract only readable body content from a PDF using font analysis.

    Returns (title, body_text).  Skips headers/footers, figure captions,
    reference lists, author bios, chart data, and other non-prose content.
    """
    from collections import Counter

    # --- Pass 1: find the dominant body-text font size ---
    size_chars = Counter()
    for page in doc:
        for b in page.get_text("dict")["blocks"]:
            if b["type"] != 0:
                continue
            for line in b["lines"]:
                for span in line["spans"]:
                    t = span["text"].strip()
                    if t:
                        size_chars[round(span["size"], 1)] += len(t)

    if not size_chars:
        # Fallback: plain extraction
        return None, "\n".join(p.get_text() for p in doc)

    body_size = size_chars.most_common(1)[0][0]
    # Accept sizes within ±1.5pt of body size (accounts for bold/italic variants)
    min_body = body_size - 1.5
    max_body = body_size + 1.5
    # Headings are larger
    max_heading = body_size * 3

    # --- Pass 2: extract body + heading text, skip the rest ---
    title = None
    paragraphs = []
    hit_references = False

    for page in doc:
        page_h = page.rect.height
        header_zone = page_h * 0.06   # top 6%
        footer_zone = page_h * 0.94   # bottom 6%

        for b in page.get_text("dict")["blocks"]:
            if b["type"] != 0:
                continue

            block_text_parts = []
            block_is_body = False
            block_is_heading = False

            for line in b["lines"]:
                for span in line["spans"]:
                    sz = span["size"]
                    y = span["bbox"][1]
                    t = span["text"].strip()
                    if not t:
                        continue

                    # Skip header/footer zones
                    if y < header_zone or y > footer_zone:
                        continue

                    # Classify by size
                    if min_body <= sz <= max_body:
                        block_is_body = True
                        block_text_parts.append(span["text"])
                    elif body_size < sz <= max_heading:
                        block_is_heading = True
                        block_text_parts.append(span["text"])
                    # else: skip (too small = footnotes/captions, too big = decorative)

            block_text = " ".join(block_text_parts).strip()
            if not block_text:
                continue

            # Grab the first large heading as the title
            if block_is_heading and not title and len(block_text) > 5:
                title = block_text[:150]

            # Stop at references / bibliography / acknowledgments
            if re.match(
                r"^(references|bibliography|works cited|endnotes|acknowledge?ments?)\s*$",
                block_text,
                re.IGNORECASE,
            ):
                hit_references = True
                break

            if hit_references:
                continue

            # Keep body paragraphs and section headings
            if block_is_body or block_is_heading:
                paragraphs.append(block_text)

        if hit_references:
            break

    return title, "\n\n".join(paragraphs)


def _extract_title(text, url):
    """Fallback title from the first non-empty line of text."""
    for line in text.split("\n"):
        line = line.strip()
        if len(line) > 5 and not line.isdigit():
            return line[:120]
    return url.split("/")[-1].replace(".pdf", "").replace("-", " ").replace("_", " ")


def _clean_text(text):
    """Clean extracted text for TTS readability."""
    # URLs, emails, DOIs
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\S+@\S+\.\S+", "", text)
    text = re.sub(r"DOI:\s*\S+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"doi\.org/\S+", "", text)
    text = re.sub(r"arXiv:\S+", "", text)

    # Citation markers: [1], [2,3], [14-16], (Author, 2020)
    text = re.sub(r"\[\d+(?:[,;\s–-]+\d+)*\]", "", text)
    text = re.sub(r"\(\w+(?:\s+(?:et\s+al\.?|and|&)\s+\w+)?,?\s*\d{4}[a-z]?\)", "", text)

    # Figure/table references
    text = re.sub(r"(?:Fig(?:ure|\.)?|Table|Eq(?:uation|\.)?)\s*\.?\s*\d+[a-z]?", "", text, flags=re.IGNORECASE)

    # Standalone numbers / page numbers
    text = re.sub(r"^\d+\s*$", "", text, flags=re.MULTILINE)

    # Copyright lines
    text = re.sub(r"©.*?\d{4}.*", "", text)
    text = re.sub(r"ACM\s+\d[\d/-]+.*", "", text)
    text = re.sub(r"Permission to (?:make|copy).*?(?:\.|$)", "", text, flags=re.DOTALL)

    # Math-heavy lines (more than 30% symbols)
    lines = []
    for line in text.split("\n"):
        if line.strip():
            alpha = sum(c.isalpha() or c.isspace() for c in line)
            if alpha / max(len(line), 1) > 0.5:
                lines.append(line)
        else:
            lines.append(line)
    text = "\n".join(lines)

    # Author affiliation lines (contain institution + location)
    text = re.sub(
        r"^.*(?:University|Institute|Department|Faculty|School of|College).*"
        r"(?:,\s*[\w ]+){1,3}\s*$",
        "", text, flags=re.MULTILINE | re.IGNORECASE,
    )

    # Keywords line
    text = re.sub(r"^(?:Keywords|Key\s*words|CCS Concepts)\s*[:.].*$", "", text, flags=re.MULTILINE | re.IGNORECASE)

    # "About the Author" / "Author Bio" sections
    text = re.sub(r"(?:^|\n\n)(?:About the Authors?|Author Bio|Biographies?).*",
                  "", text, flags=re.DOTALL | re.IGNORECASE)

    # Remaining figure/table caption sentences
    text = re.sub(r"^(?:Figure|Table|Fig\.)\s*\d+[.:]\s*.*$", "", text, flags=re.MULTILINE | re.IGNORECASE)

    # Collapse whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {2,}", " ", text)

    # Fix hyphenated line breaks (com-\nputer → computer)
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)

    return text.strip()


if __name__ == "__main__":
    print("\n  TTSSTT — PDF to Audio (Kokoro TTS)")
    print("  http://localhost:5123\n")
    app.run(debug=True, port=5123, use_reloader=False)
