#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from pyngrok import ngrok, conf
import uvicorn
import os
import shutil

# =========================
# CONFIG
# =========================
NGROK_TOKEN = "37008jtAxiSWPEdzp7OtNvmXcxv_55UUkotksc7ztTYaM2huH"
PORT = 8008
BASE_DIR = "/home/server/"

os.makedirs(BASE_DIR, exist_ok=True)
app = FastAPI()

conf.get_default().auth_token = NGROK_TOKEN

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
            links.append(f'<li>ðŸ“ <a href="/browse/{rel_path}">{f}/</a></li>')
        else:
            links.append(
                f'<li>ðŸ“„ <a href="/download/{rel_path}">{f}</a> '
                f'[<a href="/browse/{subpath}?edit={rel_path}" style="color:orange;">Edit</a>]</li>'
            )

    # --- New File UI ---
    create_form = f"""
    <div style="background: #f0f0f0; padding: 15px; border: 1px solid #ccc; margin-top: 20px;">
        <h3>Create New File</h3>
        <form action="/create" method="post">
            <input type="hidden" name="subpath" value="{subpath}">
            <input type="text" name="filename" placeholder="example.txt" required style="padding: 5px; width: 200px;">
            <button type="submit" style="padding: 5px 15px; cursor: pointer;">Create</button>
        </form>
    </div>
    """

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
                        <textarea name="content" style="width: 100%; height: 300px; font-family: monospace; background: #222; color: #0f0; padding: 10px; border: 1px solid #555;">{content}</textarea>
                        <br><br>
                        <button type="submit" style="background: #28a745; color: white; border: none; padding: 10px 20px; border-radius: 4px; cursor: pointer;">Save Changes</button>
                        <a href="/browse/{subpath}" style="color: #ccc; margin-left: 15px;">Cancel</a>
                    </form>
                </div>
                """
            except Exception as e:
                editor_html = f"<p style='color:red;'>Error reading file: {e}</p>"

    html_content = f"""
    <html>
        <head><title>File Server</title></head>
        <body style="font-family: sans-serif; padding: 20px;">
            <h2>Directory: / {subpath}</h2>
            <ul>{''.join(links)}</ul>
            <hr>
            {create_form}
            {editor_html}
        </body>
    </html>
    """
    return HTMLResponse(content=html_content)

# -------------------------
# FILE ACTIONS
# -------------------------

@app.post("/create")
def create_file(subpath: str = Form(""), filename: str = Form(...)):
    """Creates a new empty file in the current subpath."""
    # Prevent directory traversal for safety
    clean_filename = os.path.basename(filename)
    full_path = os.path.join(BASE_DIR, subpath, clean_filename)
    
    if os.path.exists(full_path):
        raise HTTPException(status_code=400, detail="File already exists")
    
    try:
        with open(full_path, "w", encoding="utf-8") as f:
            f.write("") # Create empty file
        return RedirectResponse(url=f"/browse/{subpath}?edit={os.path.join(subpath, clean_filename)}", status_code=303)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/save")
def save_file(filepath: str = Form(...), content: str = Form(...)):
    full_path = os.path.join(BASE_DIR, filepath)
    try:
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        parent_path = os.path.dirname(filepath)
        return RedirectResponse(url=f"/browse/{parent_path}", status_code=303)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/download/{filepath:path}")
def download_file(filepath: str):
    full_path = os.path.join(BASE_DIR, filepath)
    if os.path.exists(full_path) and os.path.isfile(full_path):
        return FileResponse(full_path)
    raise HTTPException(status_code=404, detail="File not found")

# -------------------------
# SERVER START
# -------------------------
if __name__ == "__main__":
    public_url = ngrok.connect(PORT).public_url
    print(f" * Public URL: {public_url}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
