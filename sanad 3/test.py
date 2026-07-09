import joblib
import json

# تحميل نموذج الذكاء الاصطناعي
model = joblib.load("sand_model.pkl")

# تحميل بيانات الحساب الوهمية
with open("account.json", "r", encoding="utf-8") as file:
    account = json.load(file)

print("=" * 40)
print("🤖 مرحبًا بك، أنا سَند")
print("كيف أستطيع مساعدتك؟")
print("اكتب exit للخروج")
print("=" * 40)

while True:
    command = input("\n👤 أنت: ")

    if command.lower() == "exit":
        print("👋 إلى اللقاء")
        break

    # معرفة نية المستخدم
    intent = model.predict([command])[0]

    # تنفيذ الطلب
    if intent == "balance":
        print(f"\n🤖 رصيدك الحالي هو {account['balance']} ريال")

    elif intent == "cards":
        print("\n🤖 بطاقاتك:")

        for card in account["cards"]:
            print(f"• {card['type']} - {card['number']}")

    elif intent == "accounts":
        print(f"\n🤖 اسم صاحب الحساب: {account['name']}")

    elif intent == "transactions":
        print("\n🤖 آخر العمليات:")

        for t in account["transactions"]:
            print(f"- {t}")

    elif intent == "notifications":
        print("\n🤖 الإشعارات:")

        for n in account["notifications"]:
            print(f"- {n}")

    elif intent == "home":
        print("\n🤖 تم الانتقال إلى الصفحة الرئيسية.")

    elif intent == "settings":
        print("\n🤖 تم فتح الإعدادات.")

    elif intent == "branch":
        print("\n🤖 تم عرض أقرب فروع مصرف الإنماء.")

    elif intent == "atm":
        print("\n🤖 تم عرض أقرب أجهزة الصراف.")

    elif intent == "beneficiary":
        print("\n🤖 تم فتح صفحة المستفيدين.")

    elif intent == "transfer":
        print("\n🤖 حفاظًا على أمان حسابك، لا يمكن تنفيذ التحويل إلا بعد التحقق بالبصمة أو رمز التحقق.")

    elif intent == "help":
        print("\n🤖 يمكنك أن تطلب مني:")
        print("- معرفة الرصيد")
        print("- عرض البطاقات")
        print("- كشف الحساب")
        print("- الإشعارات")
        print("- الحسابات")

    else:
        print("\n🤖 عذرًا، لم أفهم طلبك.")
