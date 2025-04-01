import re

def validate_phone_number(phone_number: str) -> bool:
    """
    Validate phone number format.
    Must be in E.164 format: +[country code][number]
    Example: +1234567890
    """
    pattern = r'^\+[1-9]\d{1,14}$'
    return bool(re.match(pattern, phone_number)) 