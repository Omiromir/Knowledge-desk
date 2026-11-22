import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep

import httpx

from scripts.verify_supabase_local import verify


for key in tuple(os.environ):
    if key.startswith(("KNOWLEDGE_DESK_", "OPENAI_", "RESEND_", "N8N_")):
        os.environ.pop(key, None)

with TemporaryDirectory(prefix="kd-https-check-") as directory:
    folder = Path(directory)
    verify(json.load(sys.stdin), folder)
    certificate, key = folder / "cert.pem", folder / "key.pem"
    subprocess.run([
        sys.argv[1], "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
        "-subj", "/CN=localhost", "-addext", "subjectAltName=IP:127.0.0.1",
        "-keyout", str(key), "-out", str(certificate),
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        port = candidate.getsockname()[1]
    origin = f"https://127.0.0.1:{port}"
    os.environ["KNOWLEDGE_DESK_FRONTEND_ORIGIN"] = origin
    with (folder / "uvicorn.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([
            sys.executable, "-B", "-X", "utf8", "-m", "uvicorn", "backend.app.main:app",
            "--loop", "backend.app.postgres:selector_loop", "--host", "127.0.0.1", "--port", str(port),
            "--ssl-certfile", str(certificate), "--ssl-keyfile", str(key), "--no-access-log",
        ], stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        try:
            with httpx.Client(base_url=origin, verify=False, trust_env=False, timeout=5) as server:
                deadline = monotonic() + 35
                while monotonic() < deadline and process.poll() is None:
                    try:
                        response = server.get("/health")
                    except httpx.TransportError:
                        sleep(0.25)
                        continue
                    assert response.status_code == 200 and response.json()["status"] == "ready"
                    assert response.headers["x-trace-id"]
                    home = server.get("/")
                    assert home.status_code == 200 and "/assets/" in home.text
                    assert server.get("/requests").status_code == 401
                    assert server.get("/openapi.json").status_code == 404
                    print("Live HTTPS loopback ready: health, frontend, protected requests, and disabled OpenAPI.")
                    break
                else:
                    raise RuntimeError("The isolated HTTPS process did not start")
        finally:
            if process.poll() is None:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            process.wait(timeout=5)
