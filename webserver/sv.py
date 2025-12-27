# curl -sL https://tunnelto.dev/install.sh | sh
# sudo mv /root/bin/tunnelto /usr/local/bin/
# sudo chmod +x /usr/local/bin/tunnelto

from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
import uvicorn
import os
import shutil
import subprocess
import time

# =========================
# CONFIG
# =========================
TUNNELTO_KEY = "c086afd70c4c32007c41a5cd720b8eeb"
PORT = 8008
BASE_DIR = "/home/server/"

os.makedirs(BASE_DIR, exist_ok=True)
app = FastAPI()

# -------------------------
# UI & BROWSER
# -------------------------

@app.get("/", response_class=HTMLResponse)
@app.get("/browse/{subpath:path}", response_class=HTMLResponse)
def index(subpath: str = "", edit: str = None):
    target_dir = os.path.join(BASE_DIR, subpath)
    files = os.listdir(target_dir) if os.path.exists(target_dir) else []
    
    links = []
    if subpath:
        parent = os.path.dirname(subpath)
        links.append(f'<li><b><a href="/browse/{parent}">[ .. ] Parent Directory</a></b></li>')

    for f in sorted(files):
        rel_path = os.path.join(subpath, f)
        full_p = os.path.join(BASE_DIR, rel_path)
        
        if os.path.isdir(full_p):
            links.append(f'<li>📁 <a href="/browse/{rel_path}">{f}/</a></li>')
        else:
            # Added [Edit] link next to files
            links.append(
                f'<li>📄 <a href="/download/{rel_path}">{f}</a> '
                f'[<a href="/browse/{subpath}?edit={rel_path}" style="color:orange;">Edit</a>]</li>'
            )

    # --- EDITOR SECTION ---
    editor_html = ""
    if edit:
        file_to_edit = os.path.join(BASE_DIR, edit)
        if os.path.exists(file_to_edit) and os.path.isfile(file_to_edit):
            try:
                with open(file_to_edit, "r", encoding="utf-8") as f:
                    content = f.read()
                editor_html = f"""
                <div style="background: #333; padding: 20px; border-radius: 8px; color: white; margin-top: 20px;">
                    <h3>Editing: {edit}</h3>
                    <form action="/save" method="post">
                        <input type="hidden" name="filepath" value="{edit}">
                        <textarea name="content" style="width: 100%; height: 300px; font-family: monospace; background: #222; color: #0f0; padding: 10px; border: 1px solid #555;">{content}</textarea><br>
                        <input type="submit" value="Save Changes" style="background: #28a745; color: white; border: none; padding: 10px 20px; cursor: pointer; border-radius: 4px; margin-top: 10px;">
                        <a href="/browse/{subpath}" style="color: #ccc; margin-left: 15px; text-decoration: none;">Cancel</a>
                    </form>
                </div>
                """
            except Exception as e:
                editor_html = f"<p style='color:red;'>Could not load file for editing (maybe binary?): {e}</p>"

    return f"""
    <html>
        <body style="font-family: sans-serif; padding: 20px; background: #f4f4f4;">
            <div style="max-width: 900px; margin: auto; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 4px 15px rgba(0,0,0,0.1);">
                <h2>📂 Index of / {subpath}</h2>
                <hr>
                <div style="display: flex; gap: 40px;">
                    <div style="flex: 1;">
                        <h3>Files</h3>
                        <ul style="line-height: 2;">{"".join(links)}</ul>
                        <hr>
                        <h3>Upload</h3>
                        <form action="/upload" method="post" enctype="multipart/form-data">
                            <input type="hidden" name="subpath" value="{subpath}">
                            <input type="file" name="file">
                            <input type="submit" value="Upload">
                        </form>
                    </div>
                </div>
                {editor_html}
            </div>
        </body>
    </html>
    """

# -------------------------
# ACTIONS
# -------------------------

@app.post("/save")
def save_file(filepath: str = Form(...), content: str = Form(...)):
    full_path = os.path.join(BASE_DIR, filepath)
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404)
    
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(content)
    
    # Redirect back to the folder where the file is
    parent_dir = os.path.dirname(filepath)
    return RedirectResponse(url=f"/browse/{parent_dir}", status_code=303)

@app.post("/upload")
def upload(file: UploadFile = File(...), subpath: str = Form("")):
    target_dir = os.path.join(BASE_DIR, subpath)
    os.makedirs(target_dir, exist_ok=True)
    with open(os.path.join(target_dir, file.filename), "wb") as f:
        shutil.copyfileobj(file.file, f)
    return RedirectResponse(url=f"/browse/{subpath}", status_code=303)

@app.get("/download/{filepath:path}")
def download(filepath: str):
    path = os.path.join(BASE_DIR, filepath)
    return FileResponse(path, filename=os.path.basename(path))

# -------------------------
# STARTUP
# -------------------------

if __name__ == "__main__":
    subprocess.run(["tunnelto", "set-auth", "--key", TUNNELTO_KEY], capture_output=True)
    tunnel_proc = subprocess.Popen(["tunnelto", "--port", str(PORT)])
    time.sleep(2)
    try:
        uvicorn.run(app, host="0.0.0.0", port=PORT)
    finally:
        tunnel_proc.terminate()
