#!/usr/bin/python3
"""
Transfer Manager
"""

import json
from beneficiary import BeneficiaryManager


ACCOUNT_FILE = "account.json"


class TransferManager:
    """Handle bank transfers"""

    def __init__(self):
        self.manager = BeneficiaryManager()
        self.load_account()

    def load_account(self):
        with open(ACCOUNT_FILE, "r", encoding="utf-8") as file:
            self.account = json.load(file)

    def save_account(self):
        with open(ACCOUNT_FILE, "w", encoding="utf-8") as file:
            json.dump(
                self.account,
                file,
                ensure_ascii=False,
                indent=4
            )

    def get_balance(self):
        self.load_account()
        return self.account["balance"]

    def transfer(self, beneficiary_name, amount):
        # Always work off the freshest data on disk
        self.load_account()

        beneficiary = self.manager.get(beneficiary_name)

        if beneficiary is None:
            return {
                "success": False,
                "message": "المستفيد غير مضاف"
            }

        try:
            amount = float(amount)
        except (TypeError, ValueError):
            return {
                "success": False,
                "message": "المبلغ غير صحيح"
            }

        if amount <= 0:
            return {
                "success": False,
                "message": "المبلغ غير صحيح"
            }

        if amount > self.account["balance"]:
            return {
                "success": False,
                "message": "رصيدك غير كافٍ"
            }

        self.account["balance"] -= amount

        self.account["transactions"].insert(
            0,
            f"تحويل إلى {beneficiary_name} - {amount:g} ريال"
        )

        self.account["notifications"].insert(
            0,
            f"تم تحويل {amount:g} ريال إلى {beneficiary_name}"
        )

        self.save_account()

        return {
            "success": True,
            "message": "تم التحويل بنجاح",
            "balance": self.account["balance"]
        }
