from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import os, json, uuid, io
from datetime import datetime
import numpy as np
import soundfile as sf
import librosa
from collections import defaultdict

# -------------------------------------------
# 🚀 FASTAPI INITIAL SETUP
# -------------------------------------------
app = FastAPI()

# Enable CORS so frontend can communicate
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Directory for saving logs
RAW_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")
os.makedirs(RAW_DIR, exist_ok=True)

# Dictionary to store active audio metrics temporarily
SESSION_AUDIO_DATA = defaultdict(lambda: {"volume": [], "pitch": [], "tone": [], "wpm": []})

# -------------------------------------------
# 🟢 Log a new training session (append/merge safe)
# -------------------------------------------
@app.post("/api/session/log")
async def log_session(req: Request):
    """
    Store or update session metrics + audio summaries into a JSON file.
    """
    data = await req.json()
    session_id = data.get("session_id") or f"session_{uuid.uuid4().hex}"
    data["timestamp"] = datetime.now().isoformat()

    # If real-time audio data exists for this session, aggregate it
    if session_id in SESSION_AUDIO_DATA:
        audio_data = SESSION_AUDIO_DATA[session_id]
        data["avg_volume"] = float(np.mean(audio_data["volume"])) if audio_data["volume"] else 0.0
        data["avg_pitch"] = float(np.mean(audio_data["pitch"])) if audio_data["pitch"] else 0.0
        data["avg_wpm"] = float(np.mean(audio_data["wpm"])) if audio_data["wpm"] else float(data.get("wpm", 0))
        if audio_data["tone"]:
            tones = audio_data["tone"]
            data["dominant_tone"] = max(set(tones), key=tones.count)
        else:
            data["dominant_tone"] = "Neutral"
        # clear stored audio after merging
        del SESSION_AUDIO_DATA[session_id]

    file_path = os.path.join(RAW_DIR, f"{session_id}.json")

    # merge updates if file exists
    if os.path.exists(file_path):
        with open(file_path, "r") as f:
            existing = json.load(f)
        existing.update(data)
        with open(file_path, "w") as f:
            json.dump(existing, f, indent=4)
    else:
        with open(file_path, "w") as f:
            json.dump(data, f, indent=4)

    print(f"✅ Session saved with live audio data → {file_path}")
    return {"status": "ok", "session_id": session_id}


# -------------------------------------------
# 🔊 Real-Time Audio Streaming with Progressive Storage
# -------------------------------------------
@app.websocket("/api/audio-stream")
async def audio_stream(ws: WebSocket):
    """
    Receives small audio chunks from frontend, analyzes live features,
    stores them for session-level aggregation when frontend provides session_id.
    Frontend may first send a JSON text message: {"session_id":"session_xxx"} to bind chunks.
    """
    await ws.accept()
    print("🎙️ WebSocket audio stream connected")
    sr = 16000
    session_id = None

    try:
        while True:
            msg = await ws.receive()

            # If frontend sends JSON control message with session_id
            if "text" in msg and msg["text"]:
                try:
                    control = json.loads(msg["text"])
                    if isinstance(control, dict) and control.get("session_id"):
                        session_id = control.get("session_id")
                except Exception:
                    # not JSON control — ignore
                    pass
                continue

            data = msg.get("bytes", None)
            if not data:
                continue

            # Decode PCM16 or WAV
            try:
                y, _ = sf.read(io.BytesIO(data), dtype="float32")
            except Exception:
                try:
                    y = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
                except Exception:
                    continue

            if len(y) < 100:
                continue

            # --- Feature Extraction ---
            rms = float(np.sqrt(np.mean(y ** 2)))  # loudness
            # Basic noise gate: ignore extremely quiet or extremely loud music-like chunks
            if rms < 0.001:

                # still send a small heartbeat so frontend knows server is alive
                await ws.send_json({"volume": round(rms*100,2), "pitch": 0.0, "tone": "Noise", "wpm": 0})
                continue

            try:
                pitch = float(np.mean(librosa.yin(y, 50, 400, sr=sr)))
            except Exception:
                pitch = 0.0

            tone = "Calm" if rms < 0.02 else "Balanced" if rms < 0.05 else "Energetic"
            wpm_est = int(120 + (rms * 200))

            # store in-memory for session aggregation
            if session_id:
                SESSION_AUDIO_DATA[session_id]["volume"].append(rms * 100)
                SESSION_AUDIO_DATA[session_id]["pitch"].append(pitch)
                SESSION_AUDIO_DATA[session_id]["tone"].append(tone)
                SESSION_AUDIO_DATA[session_id]["wpm"].append(wpm_est)

            # send live metrics back
            await ws.send_json({
                "volume": round(rms * 100, 2),
                "pitch": round(pitch, 1),
                "tone": tone,
                "wpm": wpm_est,
            })

    except WebSocketDisconnect:
        print("❌ WebSocket disconnected.")
    except Exception as e:
        print("⚠️ Error in audio processing:", e)
    finally:
        print("🔴 Audio WebSocket closed.")


# -------------------------------------------
# 🧠 Generate Performance Report
# -------------------------------------------
@app.get("/api/report/{session_id}")
async def get_report(session_id: str):
    file_path = os.path.join(RAW_DIR, f"{session_id}.json")

    if not os.path.exists(file_path):
        return {"error": "Session not found"}

    with open(file_path, "r") as f:
        data = json.load(f)

    # Extract metrics
    wpm = data.get("avg_wpm", data.get("wpm", 0))
    posture = float(data.get("posture", 0))
    eye_contact = data.get("eyeContact", "Unknown")
    fillers = data.get("fillers", 0)
    emotion = data.get("emotion", "Neutral")
    tone = data.get("dominant_tone", data.get("tone", "Balanced"))
    pitch = round(data.get("avg_pitch", 0), 1)
    volume = round(data.get("avg_volume", 0), 1)

    summary = f"You spoke at {int(wpm)} WPM with {fillers} filler words. Avg volume: {volume}, pitch: {pitch} Hz."

    insights = {
        "posture": f"Your posture score was {posture}%, showing {'strong alignment' if posture > 80 else 'room for improvement'}.",
        "eye_contact": f"Eye contact was {eye_contact.lower()}, indicating {'engagement' if eye_contact == 'Good' else 'inconsistent focus'}.",
        "emotion": f"Dominant facial emotion: {emotion}.",
        "tone": f"Vocal tone was mostly {tone.lower()}.",
    }

    recs = []
    if wpm < 120:
        recs.append("Increase your speaking pace slightly for energy.")
    elif wpm > 160:
        recs.append("Slow down a bit for clarity and emphasis.")
    else:
        recs.append("Pace was balanced and natural.")

    if posture < 70:
        recs.append("Maintain upright shoulders and balanced head alignment.")
    if fillers > 5:
        recs.append("Reduce filler words such as 'um' and 'like' for smoother delivery.")
    if tone.lower() == "calm":
        recs.append("Consider adding energy to sound more engaging.")
    if tone.lower() == "energetic":
        recs.append("Good vocal projection — keep it controlled for clarity.")
    if emotion.lower() in ["sad", "angry", "fearful"]:
        recs.append("A warmer tone and expression can improve connection.")

    if not recs:
        recs.append("Excellent performance overall! Keep refining consistency.")

    report = {"summary": summary, "insights": insights, "recommendations": recs}
    return {"session_id": session_id, "report": report}


# -------------------------------------------
# 🏠 Root endpoint
# -------------------------------------------
@app.get("/")
async def root():
    return {"message": "Aura Coach backend running with real-time persistent speech analysis ✅"}
