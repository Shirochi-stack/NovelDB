import random
import requests
import time

class NovelpiaAuth:
    """
    Replicates the authentication logic from Novelpia.cs.
    Manages the session state, headers, and cookie persistence.
    """
    def __init__(self):
        # Character set defined in Novelpia.cs 
        self.characters = "0123456789abcdef"
        self.session = requests.Session()
        
        # Headers replicated from MainWin.Helpers.cs PostRequest method 
        # This mimics the exact fingerprint of the legacy client.
        self.session.headers.update({
            # Match the original repo's mobile UA used in PostRequest/GET
            "User-Agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Mobile Safari/537.36",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Origin": "https://novelpia.com",
            "Referer": "https://novelpia.com/",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"
        })
        
        self.loginkey = self._generate_loginkey()
        self._update_cookie()

    def _generate_loginkey(self):
        """
        Generates the session key: 32 hex chars + '_' + 32 hex chars.
        Direct port of the LINQ Enumerable logic in Novelpia.cs.
        """
        p1 = ''.join(random.choice(self.characters) for _ in range(32))
        p2 = ''.join(random.choice(self.characters) for _ in range(32))
        return f"{p1}_{p2}"

    def _update_cookie(self):
        """Injects the LOGINKEY into the session cookies."""
        self.session.cookies.set("LOGINKEY", self.loginkey, domain="novelpia.com")

    def set_manual_key(self, key):
        """Allows the user to override the key, supporting the 'Login with Key' feature."""
        self.loginkey = key
        self._update_cookie()

    def login(self, email, password):
        """
        Performs the login handshake. 
        Replicates the exact validation logic: checking for "감사합니다" (Thank you).
        """
        url = "https://novelpia.com/proc/login"
        data = {
            "redirectrurl": "",
            "email": email,
            "wd": password
        }
        
        try:
            # High timeout to account for potential network lag
            response = self.session.post(url, data=data, timeout=15)
            # The C# code checks: if (streamReader.ReadToEnd().Contains("감사합니다")) [10]
            if "감사합니다" in response.text:
                return True
            return False
        except Exception as e:
            print(f"[Auth Error] Connection failed: {e}")
            return False