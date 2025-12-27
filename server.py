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
            links.append(f'<li>📁 <a href="/browse/{rel_path}">{f}/</a></li>')
        else:
            links.append(
                f'<li>📄 <a href="/download/{rel_path}">{f}</a> '
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
                        <textarea name="content" style="width: 100%; height: 300px; font-family: monospace; background: #222; color: #0f0; padding: 10px; border: 1px solid #555;">{content}
