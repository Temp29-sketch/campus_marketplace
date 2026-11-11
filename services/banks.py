"""Best-effort Nigerian bank codes; verify against Paystack's List Banks API before launch."""
BANKS = [
    ("Access Bank", "044"), ("GTBank", "058"), ("First Bank", "011"), ("UBA", "033"),
    ("Zenith Bank", "057"), ("Fidelity Bank", "070"), ("Ecobank", "050"), ("Sterling Bank", "232"),
    ("FCMB", "214"), ("Union Bank", "032"), ("Wema Bank", "035"), ("Unity Bank", "215"),
    ("Stanbic IBTC", "221"), ("Polaris Bank", "076"), ("Providus Bank", "101"), ("Keystone Bank", "082"),
    ("Kuda", "50211"), ("OPay", "999992"), ("PalmPay", "999991"), ("Moniepoint", "50515"),
]
BANK_BY_CODE = dict((code, name) for name, code in BANKS)

def mask_account(value):
    if not value: return ""
    return "*" * max(0, len(value) - 4) + value[-4:]
