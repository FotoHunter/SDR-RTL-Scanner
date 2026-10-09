# config/styles.py

C_RESET = '\033[0m'
C_GREEN = '\033[92m'
C_RED = '\033[91m'
C_BLUE = '\033[94m'
C_YELLOW = '\033[93m'

def fmt_ok(text: str) -> str:
    return f"{C_GREEN}{text}{C_RESET}"

def fmt_err(text: str) -> str:
    return f"{C_RED}{text}{C_RESET}"

def fmt_info(text: str) -> str:
    return f"{C_BLUE}{text}{C_RESET}"

def fmt_warn(text: str) -> str:
    return f"{C_YELLOW}{text}{C_RESET}"


