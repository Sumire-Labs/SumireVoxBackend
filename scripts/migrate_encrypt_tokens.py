# scripts/migrate_encrypt_tokens.py
import asyncio
import os
from dotenv import load_dotenv
import asyncpg
from cryptography.fernet import Fernet

load_dotenv()

async def migrate():
    encryption_key = os.environ.get("ENCRYPTION_KEY")
    if not encryption_key:
        print("ERROR: ENCRYPTION_KEY not set")
        return

    cipher = Fernet(encryption_key.encode())
    database_url = os.environ["DATABASE_URL"]

    conn = await asyncpg.connect(database_url)

    try:
        # 既存のセッションを取得
        rows = await conn.fetch("SELECT sid, access_token FROM web_sessions")
        print(f"Found {len(rows)} sessions to migrate")

        migrated = 0
        for row in rows:
            sid = row['sid']
            token = row['access_token']

            # 既に暗号化されているかチェック（Fernet トークンは gAAAAA で始まる）
            if token.startswith('gAAAAA'):
                print(f"Session {sid[:8]}... already encrypted, skipping")
                continue

            # 暗号化
            encrypted = cipher.encrypt(token.encode()).decode()

            # 更新
            await conn.execute(
                "UPDATE web_sessions SET access_token = $1 WHERE sid = $2",
                encrypted,
                sid
            )
            migrated += 1
            print(f"Migrated session {sid[:8]}...")

        print(f"Migration complete. Migrated {migrated} sessions.")

    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(migrate())
