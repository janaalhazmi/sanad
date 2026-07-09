#!/usr/bin/python3

from otp import OTPManager

otp = OTPManager()

phone = input("رقم الجوال: ")

otp.send_code(phone)

code = input("أدخل رمز التحقق: ")

if otp.verify(code):
    print("تم التحقق بنجاح")
else:
    print("رمز غير صحيح")
