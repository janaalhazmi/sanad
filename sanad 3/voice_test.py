import speech_recognition as sr

recognizer = sr.Recognizer()

try:
    with sr.Microphone() as source:
        print("🎤 تكلمي الآن...")

        recognizer.adjust_for_ambient_noise(source, duration=1)

        audio = recognizer.listen(source)

        print("⏳ جاري التعرف على الكلام...")

    text = recognizer.recognize_google(audio, language="ar-SA")

    print("\n✅ أنتِ قلتِ:")
    print(text)

except sr.UnknownValueError:
    print("\n❌ لم أستطع فهم الكلام، حاولي مرة أخرى.")

except sr.RequestError as e:
    print("\n❌ خطأ في خدمة التعرف على الصوت:")
    print(e)

except OSError as e:
    print("\n❌ مشكلة في الميكروفون:")
    print(e)

except Exception as e:
    print("\n❌ حدث خطأ غير متوقع:")
    print(e)
