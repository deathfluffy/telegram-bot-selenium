import asyncio
import base64
import hashlib
import time
from Crypto.Cipher import AES

SESSION_IDLE_SECONDS = 3600
PASSPHRASE = "default-secret"
AES_KEY = hashlib.sha256(PASSPHRASE.encode()).digest()


def encrypt_data(data: str) -> str:
    if not data:
        return ""
    cipher = AES.new(AES_KEY, AES.MODE_EAX)
    ciphertext, tag = cipher.encrypt_and_digest(data.encode("utf-8"))
    return base64.b64encode(cipher.nonce + ciphertext).decode("utf-8")


def decrypt_data(enc: str) -> str:
    if not enc:
        return ""
    try:
        raw = base64.b64decode(enc.encode("utf-8"))
        nonce, ciphertext = raw[:16], raw[16:]
        cipher = AES.new(AES_KEY, AES.MODE_EAX, nonce=nonce)
        return cipher.decrypt(ciphertext).decode("utf-8")
    except Exception:
        return ""


class SecureSessionManager:
    def __init__(self):
        self.sessions = {}
        self.lock = asyncio.Lock()
        self._cleanup_task = None
        self._cleanup_running = False

    async def start_cleanup(self):
        """Start the periodic cleanup task"""
        if self._cleanup_task is None and not self._cleanup_running:
            self._cleanup_task = asyncio.create_task(self._periodic_cleanup())
            self._cleanup_running = True

    async def stop_cleanup(self):
        """Stop the periodic cleanup task"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
            self._cleanup_running = False

    async def create_session(self, user_id: int, data: dict) -> bool:
        async with self.lock:
            if user_id in self.sessions and self.sessions[user_id].get("driver"):
                return False
            self.sessions[user_id] = {
                "login": encrypt_data(data.get("login", "")),
                "password": encrypt_data(data.get("password", "")),
                "created_at": time.time(),
                "last_activity": time.time(),
                "driver": data.get("driver"),
            }
            return True

    async def get_session(self, user_id: int):
        async with self.lock:
            sess = self.sessions.get(user_id)
            if not sess:
                return None
            sess["last_activity"] = time.time()
            return {
                **sess,
                "login": decrypt_data(sess["login"]),
                "password": decrypt_data(sess["password"]),
            }

    async def update_session_data(self, user_id: int, session_data: dict):
        async with self.lock:
            if user_id in self.sessions:
                self.sessions[user_id].update({
                    "login": encrypt_data(session_data.get("login", "")),
                    "password": encrypt_data(session_data.get("password", "")),
                    "last_activity": time.time(),
                    "driver": session_data.get("driver"),
                })

    async def update_driver(self, user_id: int, driver):
        async with self.lock:
            if user_id in self.sessions:
                self.sessions[user_id]["driver"] = driver
                self.sessions[user_id]["last_activity"] = time.time()

    async def delete_session(self, user_id: int):
        async with self.lock:
            sess = self.sessions.pop(user_id, None)
            if sess and sess.get("driver"):
                try:
                    sess["driver"].quit()
                except Exception:
                    pass

    async def cleanup_all_sessions(self) -> int:
        async with self.lock:
            count = 0
            now = time.time()
            for uid in list(self.sessions.keys()):
                if now - self.sessions[uid]["last_activity"] > SESSION_IDLE_SECONDS:
                    await self.delete_session(uid)
                    count += 1
            return count

    async def _periodic_cleanup(self):
        """Background task that cleans up idle sessions"""
        while True:
            try:
                await asyncio.sleep(60)  # wait first to avoid immediate cleanup
                count = await self.cleanup_all_sessions()
                if count > 0:
                    print(f"[cleanup] Removed {count} idle sessions")
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[cleanup error] {e}")
                await asyncio.sleep(60)  # wait before retrying on error


# global instance
session_manager = SecureSessionManager()