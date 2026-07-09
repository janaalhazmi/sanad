#!/usr/bin/python3
"""
Beneficiary Manager
"""

import json
import os


FILE_NAME = "beneficiaries.json"


class BeneficiaryManager:
    """Manage beneficiaries"""

    def __init__(self):
        self.beneficiaries = []
        self.load()

    def load(self):
        """Load beneficiaries"""

        if os.path.exists(FILE_NAME):

            with open(FILE_NAME, "r", encoding="utf-8") as file:

                self.beneficiaries = json.load(file)

        else:

            self.save()

    def save(self):
        """Save beneficiaries"""

        with open(FILE_NAME, "w", encoding="utf-8") as file:

            json.dump(
                self.beneficiaries,
                file,
                ensure_ascii=False,
                indent=4
            )

    def add(self, name, iban, nickname):
        """Add beneficiary"""

        if self.exists(name):

            return False

        self.beneficiaries.append({

            "name": name,

            "iban": iban,

            "nickname": nickname

        })

        self.save()

        return True

    def exists(self, name):
        """Check if beneficiary exists"""

        for beneficiary in self.beneficiaries:

            if beneficiary["name"] == name:

                return True

        return False

    def remove(self, name):
        """Remove beneficiary"""

        for beneficiary in self.beneficiaries:

            if beneficiary["name"] == name:

                self.beneficiaries.remove(beneficiary)

                self.save()

                return True

        return False

    def get(self, name):
        """Return beneficiary"""

        for beneficiary in self.beneficiaries:

            if beneficiary["name"] == name:

                return beneficiary

        return None

    def get_all(self):
        """Return all beneficiaries"""

        return self.beneficiaries
