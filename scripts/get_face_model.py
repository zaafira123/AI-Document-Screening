"""Optional: download the SFace face-recognition model (much stronger than the built-in demo matcher).
Run once:  python scripts/get_face_model.py   then restart the server."""
import os, sys, urllib.request
URL = "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx"
DEST = os.path.join(os.path.dirname(__file__), "..", "models", "face_recognition_sface_2021dec.onnx")
os.makedirs(os.path.dirname(DEST), exist_ok=True)
try:
    urllib.request.urlretrieve(URL, DEST)
    print("saved", os.path.abspath(DEST), os.path.getsize(DEST), "bytes")
except Exception as e:
    print("download failed:", e); sys.exit(1)
