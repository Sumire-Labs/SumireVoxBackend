import os
from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

_key = os.environ.get("ENCRYPTION_KEY")
if not _key:
    # 開発環境で未設定の場合のフォールバック（本番では必ず設定すること）
    _key = Fernet.generate_key().decode()

_fernet = Fernet(_key.encode())

def encrypt(text: str) -> str:
    if not text:
        return text
    return _fernet.encrypt(text.encode()).decode()

def decrypt(token: str) -> str:
    if not token:
        return token
    try:
        return _fernet.decrypt(token.encode()).decode()
    except Exception:
        # 復号失敗時（古い平文データなど）はそのまま返すかエラーにする
        # 移行期はそのまま返す設計も検討できるが、基本は例外
        return token
