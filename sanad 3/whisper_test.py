import whisper

model = whisper.load_model("medium")

result = model.transcribe(
    "voice.wav",
    language="ar",
    task="transcribe"
)

print(result["text"])
