import app_server
from ai import ai_engine

CANNED = {
    "ما رسوم السحب النقدي من الخارج": {"type": "text", "text": "رسوم السحب من الصراف الآلي خارج المملكة تختلف حسب نوع بطاقتك، عادة تتراوح بين ١٠-٧٥ ريال بالإضافة لرسوم تحويل العملة. هل تريد التحقق من رسوم بطاقتك تحديداً؟"},
    "ودي أحول ميتين ريال لأحمد بسرعة": {"type": "tool", "tool": "propose_transfer", "args": {"beneficiary_name": "أحمد", "amount": 200}},
    "حول فلوس لشخص اسمه غير موجود اطلاقا": {"type": "tool", "tool": "propose_transfer", "args": {"beneficiary_name": "غير موجود اطلاقا", "amount": 50}},
    "ابغى أضيف شخص جديد كمستفيد اسمه سالم وآيبانه SA123456789": {"type": "tool", "tool": "propose_add_beneficiary", "args": {"name": "سالم", "iban": "SA123456789", "nickname": "سالم"}},
}

def fake_get_ai_reply(message, session, account, current_page="unknown", senior_mode=False):
    return CANNED.get(message.strip(), {"unavailable": True})

ai_engine.get_ai_reply = fake_get_ai_reply
app_server.app.run(host="127.0.0.1", port=5001, debug=False, use_reloader=False)
