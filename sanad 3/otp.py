#!/usr/bin/python3

import random
import time


class OTPManager:

    def __init__(self):
        self.code = None
        self.expire_time = None

    def send_code(self, phone):

        self.code = str(random.randint(100000, 999999))

        self.expire_time = time.time() + 120

        print("\n==============================")
        print("تم إرسال رمز التحقق")
        print(f"رقم الجوال: {phone}")
        print(f"رمز التحقق: {self.code}")
        print("==============================\n")

    def verify(self, entered_code):

        if self.code is None or self.expire_time is None:
            print("لم يتم إرسال أي رمز تحقق بعد")
            return False

        if time.time() > self.expire_time:
            print("انتهت صلاحية الرمز")
            return False

        return entered_code == self.code
