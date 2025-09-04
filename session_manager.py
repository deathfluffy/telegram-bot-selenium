import time
import threading
import base64
import hashlib
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
        self.session_timeouts = {}
        self.lock = threading.RLock()

    def create_session(self, user_id: int, data: dict):
        with self.lock:
            if user_id in self.sessions and self.sessions[user_id].get("driver"):
                # User already logged in
                return False
            self.sessions[user_id] = {
                "login": encrypt_data(data.get("login", "")),
                "password": encrypt_data(data.get("password", "")),
                "created_at": time.time(),
                "last_activity": time.time(),
                "driver": data.get("driver"),
            }
            self._reset_timer(user_id)
            return True

    def get_session(self, user_id: int):
        with self.lock:
            sess = self.sessions.get(user_id)
            if not sess:
                return None
            sess["last_activity"] = time.time()
            self._reset_timer(user_id)
            return {
                **sess,
                "login": decrypt_data(sess["login"]),
                "password": decrypt_data(sess["password"]),
            }

    def update_session_data(self, user_id: int, session_data: dict):
        with self.lock:
            if user_id in self.sessions:
                self.sessions[user_id].update({
                    "login": encrypt_data(session_data.get("login", "")),
                    "password": encrypt_data(session_data.get("password", "")),
                    "last_activity": time.time(),
                    "driver": session_data.get("driver"),
                })
                self._reset_timer(user_id)

    def update_driver(self, user_id: int, driver):
        with self.lock:
            if user_id in self.sessions:
                self.sessions[user_id]["driver"] = driver
                self.sessions[user_id]["last_activity"] = time.time()
                self._reset_timer(user_id)

    def delete_session(self, user_id: int):
        with self.lock:
            sess = self.sessions.pop(user_id, None)
            if sess and sess.get("driver"):
                try:
                    sess["driver"].quit()
                except Exception:
                    pass
            timer = self.session_timeouts.pop(user_id, None)
            if timer:
                try:
                    timer.cancel()
                except Exception:
                    pass

    def cleanup_all_sessions(self) -> int:
        with self.lock:
            count = 0
            now = time.time()
            for uid in list(self.sessions.keys()):
                if now - self.sessions[uid]["last_activity"] > SESSION_IDLE_SECONDS:
                    self.delete_session(uid)
                    count += 1
            return count

    def _reset_timer(self, user_id: int):
        old = self.session_timeouts.get(user_id)
        if old:
            try:
                old.cancel()
            except Exception:
                pass
        t = threading.Timer(SESSION_IDLE_SECONDS, self._cleanup_session, [user_id])
        t.daemon = True
        self.session_timeouts[user_id] = t
        t.start()

    def _cleanup_session(self, user_id: int):
        with self.lock:
            sess = self.sessions.get(user_id)
            if not sess:
                return
            if time.time() - sess["last_activity"] > SESSION_IDLE_SECONDS:
                self.delete_session(user_id)

# global instance
session_manager = SecureSessionManager()
