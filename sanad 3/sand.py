import asyncio
import json
import os
import random
from difflib import get_close_matches

import edge_tts
import joblib
import whisper
import re

from beneficiary import BeneficiaryManager
from transfer import TransferManager
from otp import OTPManager
# -----------------------------
# Text To Speech
# -----------------------------
async def speak(text):

    communicate = edge_tts.Communicate(
        text=text,
        voice="ar-SA-ZariyahNeural"
    )

    await communicate.save("response.mp3")

    os.system("afplay response.mp3")


# -----------------------------
# Load AI Models
# -----------------------------
print("Loading AI models...")

speech_model = whisper.load_model("medium")

ai_model = joblib.load("sand_model.pkl")

print("Models loaded successfully.")


# -----------------------------
# Load Account Data
# -----------------------------
with open(
    "account.json",
    "r",
    encoding="utf-8"
) as file:

    account = json.load(file)
# -----------------------------
# Banking System
# -----------------------------
beneficiaries = BeneficiaryManager()

transfer_manager = TransferManager()

otp = OTPManager()

# -----------------------------

# Transfer Helper

# -----------------------------

def extract_transfer(text):

    pattern = r"حول\s+(\d+)\s+(?:ريال)?\s*(?:الى|إلى)?\s*(.+)"

    result = re.search(pattern, text)

    if result:

        amount = float(result.group(1))

        name = result.group(2).strip()

        return amount, name

    return None, None

# -----------------------------
# Conversation Memory
# -----------------------------
conversation_memory = {

    "last_intent": None,

    "last_response": None,

    "user_name": account["name"]

}


# -----------------------------
# Random Responses
# -----------------------------
balance_responses = [

    f"أهلًا {account['name']}، رصيدك الحالي هو {account['balance']} ريال. هل ترغب في الاطلاع على آخر العمليات؟",

    f"تم العثور على بيانات حسابك. رصيدك الحالي هو {account['balance']} ريال.",

    f"رصيدك المتاح الآن هو {account['balance']} ريال. هل أستطيع مساعدتك بشيء آخر؟"

]


cards_responses = [

    "تم فتح صفحة البطاقات.",

    "هذه جميع بطاقاتك المسجلة.",

    "تم استعراض بطاقاتك بنجاح."

]


transactions_responses = [

    "تم فتح كشف الحساب.",

    "هذه آخر العمليات المنفذة على حسابك.",

    "إليك أحدث العمليات المالية."

]


help_responses = [

    "يمكنني مساعدتك في الرصيد والبطاقات وكشف الحساب والفروع والصرافات.",

    "اطلب أي خدمة مصرفية وسأساعدك.",

    "كيف أستطيع مساعدتك اليوم؟"

]


# -----------------------------
# Voice Recognition
# -----------------------------
print("\n🎤 Listening...")

result = speech_model.transcribe(

    "voice.wav",

    language="ar",

    task="transcribe"

)

text = result["text"].strip()

print("\n👤 You said:")

print(text)
# -----------------------------
# Supported Commands
# -----------------------------
commands = [

    "كم رصيدي",
    "كم الرصيد",
    "وش رصيدي",
    "كم معي",
    "اعرض الرصيد",
    "أبي أشوف رصيدي",

    "افتح البطاقات",
    "بطاقاتي",
    "اعرض البطاقات",
    "ورني بطاقاتي",

    "كشف الحساب",
    "آخر العمليات",
    "اعرض آخر العمليات",
    "ورني العمليات",

    "الرئيسية",
    "ارجع للرئيسية",
    "افتح الصفحة الرئيسية",

    "الإعدادات",

    "الإشعارات",

    "وين أقرب صراف",

    "وين أقرب فرع",

    "حول مبلغ",

    "ساعدني"

]


# -----------------------------
# Command Correction
# -----------------------------
match = get_close_matches(

    text,

    commands,

    n=1,

    cutoff=0.35

)

if match:

    corrected_text = match[0]

    print("\n✅ Corrected command:")

    print(corrected_text)

else:

    corrected_text = text


# -----------------------------
# Follow-up Conversation
# -----------------------------
if corrected_text in [

    "والبطاقات",

    "البطاقات",

    "طيب البطاقات"

]:

    corrected_text = "افتح البطاقات"

elif corrected_text in [

    "والرصيد",

    "الرصيد",

    "طيب الرصيد"

]:

    corrected_text = "كم رصيدي"

elif corrected_text in [

    "والعمليات",

    "العمليات",

    "طيب العمليات"

]:

    corrected_text = "كشف الحساب"

elif corrected_text in [

    "الرئيسية",

    "ارجع"

]:

    corrected_text = "افتح الصفحة الرئيسية"


# -----------------------------
# Predict Intent
# -----------------------------
intent = ai_model.predict(

    [corrected_text]

)[0]

conversation_memory["last_intent"] = intent
# -----------------------------
# Build Smart Response
# -----------------------------

if intent == "balance":

    response = random.choice(balance_responses)

    response += "\n\nهل ترغب في الاطلاع على البطاقات أو آخر العمليات؟"


elif intent == "cards":

    cards = []

    for card in account["cards"]:

        cards.append(

            f"{card['type']} المنتهية بـ {card['number']}"

        )

    response = random.choice(cards_responses)

    response += "\n"

    response += "\n".join(cards)

    response += "\n\nهل ترغب في معرفة آخر العمليات؟"


elif intent == "transactions":

    response = random.choice(transactions_responses)

    response += "\n\n"

    for operation in account["transactions"]:

        response += f"• {operation}\n"

    response += "\nهل ترغب في العودة إلى الصفحة الرئيسية؟"


elif intent == "home":

    response = (

        "تم الانتقال إلى الصفحة الرئيسية.\n\n"

        "يمكنك معرفة:\n"

        "• الرصيد\n"

        "• البطاقات\n"

        "• كشف الحساب\n"

        "• الفروع\n"

        "• أجهزة الصراف"

    )


elif intent == "notifications":

    response = (

        "لا توجد إشعارات جديدة حالياً.\n\n"

        "سيتم إعلامك عند وجود أي إشعار جديد."

    )


elif intent == "settings":

    response = (

        "تم فتح صفحة الإعدادات.\n\n"

        "يمكنك تعديل:\n"

        "• المعلومات الشخصية\n"

        "• كلمة المرور\n"

        "• إعدادات الأمان"

    )


elif intent == "branch":

    response = (

        "تم العثور على أقرب فرع لمصرف الإنماء.\n\n"

        "هل ترغب في معرفة ساعات العمل؟"

    )


elif intent == "atm":

    response = (

        "تم العثور على أقرب جهاز صراف آلي.\n\n"

        "يمكنني أيضاً عرض أقرب فرع."

    )


elif intent == "transfer":

    amount, beneficiary_name = extract_transfer(corrected_text)

    if amount is None:

        beneficiary_name = input("اسم المستفيد: ")

        amount = float(input("المبلغ: "))

    if not beneficiaries.exists(beneficiary_name):

        answer = input(
            "المستفيد غير مضاف.\nهل تريد إضافته؟ (نعم/لا): "
        )

        if answer in [

            "نعم",

            "اي",

            "إيه"

        ]:

            iban = input("رقم الآيبان: ")

            full_name = input("اسم المستفيد: ")

            nickname = input("الاسم المختصر: ")

            beneficiaries.add(

                full_name,

                iban,

                nickname

            )

            print("تمت إضافة المستفيد.")

        else:

            response = "تم إلغاء العملية."

            print(response)

            asyncio.run(
                speak(response)
            )

            exit()

    phone = input("رقم الجوال: ")

    otp.send_code(phone)

    code = input("رمز التحقق: ")

    if not otp.verify(code):

        response = "رمز التحقق غير صحيح."

        print(response)

        asyncio.run(
            speak(response)
        )

        exit()

    input(
        "ضع إصبعك على البصمة ثم اضغط Enter..."
    )

    result = transfer_manager.transfer(

        beneficiary_name,

        amount

    )

    response = result["message"]

            

            
       


elif intent == "help":

    response = random.choice(help_responses)


else:

    response = (

        "عذراً، لم أفهم طلبك.\n\n"

        "يمكنك سؤالي عن:\n"

        "• الرصيد\n"

        "• البطاقات\n"

        "• كشف الحساب\n"

        "• الفروع\n"

        "• الصرافات"

    )


conversation_memory["last_response"] = response
# -----------------------------
# Greeting Based On Time
# -----------------------------
from datetime import datetime

hour = datetime.now().hour

if hour < 12:

    greeting = "صباح الخير"

elif hour < 18:

    greeting = "مساء الخير"

else:

    greeting = "مساء الخير"


# -----------------------------
# Follow-up Conversation
# -----------------------------
yes_words = [

    "نعم",
    "اي",
    "إيه",
    "أكيد",
    "اكيد",
    "طبعاً",
    "طيب"

]

no_words = [

    "لا",
    "لا شكرا",
    "شكراً",
    "شكرا",
    "لا أبي",
    "لا ابغى"

]


if corrected_text in yes_words:

    if conversation_memory["last_intent"] == "balance":

        response = (

            "رائع.\n\n"

            "هل ترغب في عرض:\n"

            "• البطاقات\n"

            "• آخر العمليات"

        )

    elif conversation_memory["last_intent"] == "cards":

        response = (

            "تم.\n\n"

            "هل ترغب أيضاً في الاطلاع على آخر العمليات؟"

        )

    elif conversation_memory["last_intent"] == "transactions":

        response = (

            "هل ترغب بالعودة إلى الصفحة الرئيسية؟"

        )

elif corrected_text in no_words:

    response = (

        f"{greeting} {account['name']}.\n\n"

        "يسعدني مساعدتك في أي وقت."

    )


# -----------------------------
# Chat Statistics
# -----------------------------
if "chat_counter" not in conversation_memory:

    conversation_memory["chat_counter"] = 0

conversation_memory["chat_counter"] += 1


# -----------------------------
# Session Information
# -----------------------------
conversation_memory["last_response"] = response

print("\n----------------------------")

print("Conversation Number:")

print(conversation_memory["chat_counter"])

print("----------------------------")
# -----------------------------
# AI Thinking Animation
# -----------------------------
import time

print("\n🤖 سَند يفكر", end="")

for _ in range(3):

    time.sleep(0.4)

    print(".", end="", flush=True)

print("\n")


# -----------------------------
# Print Response
# -----------------------------
print("=" * 50)

print("🤖 سَند")

print("=" * 50)

print(response)

print("=" * 50)


# -----------------------------
# Speak Response
# -----------------------------
asyncio.run(
    speak(response)
)


# -----------------------------
# Session Summary
# -----------------------------
print("\n📊 Session Summary")

print("-----------------------------")

print("Detected Intent:")

print(intent)

print("\nCorrected Command:")

print(corrected_text)

print("\nConversation Count:")

print(conversation_memory["chat_counter"])

print("-----------------------------")


# -----------------------------
# Suggestions
# -----------------------------
suggestions = {

    "balance": [
        "اعرض البطاقات",
        "اعرض آخر العمليات"
    ],

    "cards": [
        "كم رصيدي",
        "اعرض آخر العمليات"
    ],

    "transactions": [
        "كم رصيدي",
        "افتح الصفحة الرئيسية"
    ],

    "home": [
        "كم رصيدي",
        "اعرض البطاقات"
    ]

}


if intent in suggestions:

    print("\n💡 Suggestions:")

    for item in suggestions[intent]:

        print(f"• {item}")


print("\n✅ Request completed successfully.")

