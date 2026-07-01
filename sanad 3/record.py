import sounddevice as sd
import soundfile as sf

duration = 5  # مدة التسجيل بالثواني
sample_rate = 16000

print("🎤 تكلمي الآن...")

recording = sd.rec(
    int(duration * sample_rate),
    samplerate=sample_rate,
    channels=1,
    dtype="int16"
)

sd.wait()

sf.write("voice.wav", recording, sample_rate)

print("✅ تم حفظ التسجيل باسم voice.wav")
