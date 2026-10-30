"""Print which face engine is active."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from docscreen.modules import face
print("SFace model present:", face.SFACE_PATH.exists())
try: print("active engine:", face.get_engine().name)
except Exception as e: print("engine lookup:", e)
