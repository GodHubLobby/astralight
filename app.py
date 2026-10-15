import os
import io
import shutil
import tempfile
import json
import time
import urllib.request
import urllib.parse
from flask import Flask, render_template, request, send_file, jsonify, Response
from PIL import Image
import yt_dlp

app = Flask(__name__)

# Global stats and history storage
STATS = {
    "total": 0,
    "success": 0,
    "total_time": 0.0,
    "history": []
}

@app.route("/")
def index():
    avg_time = round(STATS["total_time"] / STATS["success"], 1) if STATS["success"] > 0 else 0
    success_rate = round((STATS["success"] / STATS["total"]) * 100) if STATS["total"] > 0 else 100
    
    return render_template(
        "index.html", 
        total=STATS["total"], 
        success=STATS["success"], 
        avg_time=avg_time, 
        success_rate=success_rate,
        history=STATS["history"][:10]
    )

COMPLETED_FILES = {}

# ---------------------------------------------------------
# 1. Image to PDF Route (Instant Logging)
# ---------------------------------------------------------
@app.route("/convert-pdf", methods=["POST"])
def convert_pdf():
    start_time = time.time()
    STATS["total"] += 1

    if "image" not in request.files:
        STATS["history"].insert(0, {"name": "Upload", "type": "Image to PDF", "time": "-", "status": "Failed"})
        return jsonify({"error": "Please choose an image file."}), 400
    
    file = request.files["image"]
    if file.filename == "":
        STATS["history"].insert(0, {"name": "File", "type": "Image to PDF", "time": "-", "status": "Failed"})
        return jsonify({"error": "No file selected."}), 400

    try:
        image = Image.open(file.stream).convert("RGB")
        pdf_buffer = io.BytesIO()
        image.save(pdf_buffer, format="PDF", resolution=100.0)
        pdf_buffer.seek(0)
        
        output_filename = os.path.splitext(file.filename)[0] + ".pdf"
        output_path = os.path.join(tempfile.mkdtemp(), output_filename)
        with open(output_path, "wb") as f:
            f.write(pdf_buffer.getvalue())

        duration = round(time.time() - start_time, 1)
        STATS["success"] += 1
        STATS["total_time"] += duration
        STATS["history"].insert(0, {"name": output_filename, "type": "Image to PDF", "time": f"{duration}s", "status": "Success"})

        file_id = output_filename
        COMPLETED_FILES[file_id] = output_path
        return jsonify({"success": True, "file_id": file_id})
    except Exception as e:
        STATS["history"].insert(0, {"name": file.filename, "type": "Image to PDF", "time": "-", "status": "Failed"})
        return jsonify({"error": str(e)}), 500

# ---------------------------------------------------------
# 2. PDF to Image Route (Instant Logging)
# ---------------------------------------------------------
@app.route("/convert-pdf-to-image", methods=["POST"])
def convert_pdf_to_image():
    start_time = time.time()
    STATS["total"] += 1

    if "pdf" not in request.files:
        STATS["history"].insert(0, {"name": "Upload", "type": "PDF to Image", "time": "-", "status": "Failed"})
        return jsonify({"error": "Please choose a PDF file."}), 400
    
    file = request.files["pdf"]
    img_format = request.form.get("format", "png").upper()

    if file.filename == "":
        STATS["history"].insert(0, {"name": "File", "type": "PDF to Image", "time": "-", "status": "Failed"})
        return jsonify({"error": "No file selected."}), 400

    temp_dir = tempfile.mkdtemp()
    try:
        import fitz # PyMuPDF
        
        pdf_bytes = file.read()
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        if len(doc) == 0:
            raise Exception("PDF file is empty.")
        
        page = doc[0]
        pix = page.get_pixmap(dpi=150)
        
        ext = "png" if img_format == "PNG" else "jpg"
        output_filename = os.path.splitext(file.filename)[0] + f"_page1.{ext}"
        output_path = os.path.join(temp_dir, output_filename)
        
        pix.save(output_path)

        duration = round(time.time() - start_time, 1)
        STATS["success"] += 1
        STATS["total_time"] += duration
        STATS["history"].insert(0, {"name": output_filename, "type": f"PDF to {img_format}", "time": f"{duration}s", "status": "Success"})

        file_id = output_filename
        COMPLETED_FILES[file_id] = output_path
        return jsonify({"success": True, "file_id": file_id})

    except Exception as e:
        STATS["history"].insert(0, {"name": file.filename, "type": "PDF to Image", "time": "-", "status": "Failed"})
        return jsonify({"error": f"Requires PyMuPDF package (pip install PyMuPDF). Details: {str(e)}"}) , 500

# ---------------------------------------------------------
# 3. YouTube Progress Route (MP4 or MP3)
# ---------------------------------------------------------
@app.route("/download-yt-stream", methods=["POST"])
def download_yt_stream():
    data = request.json
    url = data.get("url")
    kind = data.get("kind", "mp4")

    if not url:
        return jsonify({"error": "Please paste a YouTube link."}), 400

    STATS["total"] += 1
    start_time = time.time()

    def generate():
        temp_dir = tempfile.mkdtemp()
        try:
            yield f"data: {json.dumps({'progress': 20, 'status': 'Getting video info...'})}\n\n"
            
            fmt = "bestaudio/best" if kind == "mp3" else "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
            postprocessors = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}] if kind == "mp3" else []
            
            yield f"data: {json.dumps({'progress': 50, 'status': 'Downloading file...'})}\n\n"

            ydl_opts = {
                "format": fmt,
                "outtmpl": os.path.join(temp_dir, "%(title).50s.%(ext)s"),
                "postprocessors": postprocessors,
                "http_headers": {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                },
                "extractor_args": {
                    "youtube": {
                        "player_client": ["android"]
                    }
                },
                "remote_components": ["ejs:npm"]
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
                if kind == "mp3":
                    filename = os.path.splitext(filename)[0] + ".mp3"

            duration = round(time.time() - start_time, 1)
            STATS["success"] += 1
            STATS["total_time"] += duration
            
            file_id = os.path.basename(filename)
            COMPLETED_FILES[file_id] = filename
            
            STATS["history"].insert(0, {"name": file_id, "type": f"YouTube ({kind.upper()})", "time": f"{duration}s", "status": "Success"})

            yield f"data: {json.dumps({'progress': 100, 'status': 'Done!', 'file_id': file_id})}\n\n"
        except Exception as e:
            STATS["history"].insert(0, {"name": "YouTube Link", "type": f"YouTube ({kind.upper()})", "time": "-", "status": "Failed"})
            shutil.rmtree(temp_dir, ignore_errors=True)
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return Response(generate(), mimetype="text/event-stream")

# ---------------------------------------------------------
# 4. Spotify Progress Route (MP3)
# ---------------------------------------------------------
@app.route("/download-spotify-stream", methods=["POST"])
def download_spotify_stream():
    data = request.json
    url = data.get("url")

    if not url:
        return jsonify({"error": "Please paste a Spotify link."}), 400

    STATS["total"] += 1
    start_time = time.time()

    def generate():
        temp_dir = tempfile.mkdtemp()
        try:
            yield f"data: {json.dumps({'progress': 20, 'status': 'Finding song info...'})}\n\n"

            oembed_url = f"https://open.spotify.com/oembed?url={urllib.parse.quote(url)}"
            req = urllib.request.Request(oembed_url, headers={"User-Agent": "Mozilla/5.0"})
            
            search_query = None
            try:
                with urllib.request.urlopen(req) as response:
                    data_json = json.loads(response.read().decode())
                    track_title = data_json.get("title", "")
                    if track_title:
                        search_query = f"ytsearch1:{track_title} audio"
            except Exception:
                pass

            if not search_query:
                search_query = f"ytsearch1:{url} audio"

            yield f"data: {json.dumps({'progress': 50, 'status': 'Downloading MP3 audio...'})}\n\n"

            ydl_opts = {
                "format": "bestaudio/best",
                "outtmpl": os.path.join(temp_dir, "%(title).50s.%(ext)s"),
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }],
                "http_headers": {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                },
                "extractor_args": {
                    "youtube": {
                        "player_client": ["android"]
                    }
                },
                "remote_components": ["ejs:npm"]
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(search_query, download=True)
                if "entries" in info:
                    info = info["entries"][0]
                
                filename = ydl.prepare_filename(info)
                filename = os.path.splitext(filename)[0] + ".mp3"

            if not os.path.isfile(filename):
                raise Exception("Could not create MP3 file.")

            duration = round(time.time() - start_time, 1)
            STATS["success"] += 1
            STATS["total_time"] += duration

            file_id = os.path.basename(filename)
            COMPLETED_FILES[file_id] = filename
            
            STATS["history"].insert(0, {"name": file_id, "type": "Spotify MP3", "time": f"{duration}s", "status": "Success"})

            yield f"data: {json.dumps({'progress': 100, 'status': 'Done!', 'file_id': file_id})}\n\n"
        except Exception as e:
            STATS["history"].insert(0, {"name": "Spotify Track", "type": "Spotify MP3", "time": "-", "status": "Failed"})
            shutil.rmtree(temp_dir, ignore_errors=True)
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return Response(generate(), mimetype="text/event-stream")

@app.route("/download-file/<file_id>")
def download_file(file_id):
    path = COMPLETED_FILES.get(file_id)
    if path and os.path.isfile(path):
        return send_file(path, as_attachment=True)
    return "File not found or expired", 404

if __name__ == "__main__":
    app.run(debug=True, port=5000)
