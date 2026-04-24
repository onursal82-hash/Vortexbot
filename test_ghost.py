import requests

BASE_URL = "http://127.0.0.1:5300"

def test_api():
    print("--- Testing Ghost Bot Cleanup ---")
    s = requests.Session()
    s.trust_env = False
    
    r = s.post(f"{BASE_URL}/api/login", json={"email": "test@test.com"})
    print("Login:", r.status_code)
    
    r = s.post(f"{BASE_URL}/api/cleanup_bots")
    print("Cleanup:", r.status_code, r.text)

if __name__ == "__main__":
    test_api()
