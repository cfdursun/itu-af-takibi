import os
from flask import Flask, render_template, redirect, url_for, jsonify, session
from bs4 import BeautifulSoup
import requests
from datetime import datetime
import pytz
from google import genai
from google.genai import types
import json
import time
import uuid

app = Flask(__name__)
app.secret_key = "itu_af_takip_secret_key"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

COOLDOWN_SECONDS = 15
last_request_timestamp = 0
last_request_user = None

MODEL_NAME = "gemini-3.5-flash-lite"

NAV_BLACKLIST = [
    "öbs giriş", "itü anasayfa", "ninova", "program bilgi paketi", 
    "program bilgi paketi / tyyç", "öğrenci dekanlığı", "yardım", 
    "iletişim", "english", "hakkımızda", "öbs hakkında", "öğrenci", 
    "kayıt süreçleri", "mevzuat", "başvuru ve kabul", "anasayfa", "duyurular", "tarihçe"
]

site_state = {
    "started": False,
    "message": "Henüz kontrol yapılmadı.",
    "short_status": "Beklemede",
    "last_3": [],
    "last_checked": "-"
}

def analyze_announcements_with_ai():
    global site_state, last_request_timestamp, last_request_user
    
    if not GEMINI_API_KEY:
        site_state["started"] = False
        site_state["message"] = "API Anahtarı bulunamadı (GEMINI_API_KEY tanımlanmamış)."
        site_state["short_status"] = "API Key Eksik"
        return

    client = genai.Client(api_key=GEMINI_API_KEY)

    list_url = "https://www.sis.itu.edu.tr/TR/duyurular/duyurular.php"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    
    try:
        response = requests.get(list_url, headers=headers, timeout=10)
        response.encoding = "utf-8"
        
        latest_3 = []
        detail_content = ""

        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")
            announcements = []

            for li in soup.find_all("li"):
                a_tag = li.find("a")
                font_tag = li.find("font")
                
                if a_tag and font_tag:
                    text = a_tag.get_text().strip()
                    href = a_tag.get("href", "")
                    
                    if text and text.lower() not in NAV_BLACKLIST and len(text) > 5:
                        full_url = href if href.startswith("http") else f"https://www.sis.itu.edu.tr/TR/duyurular/{href}"
                        if not any(a["title"] == text for a in announcements):
                            announcements.append({"title": text, "url": full_url})

            if not announcements:
                for a_tag in soup.find_all("a", href=True):
                    text = a_tag.get_text().strip()
                    href = a_tag.get("href", "")
                    if text and text.lower() not in NAV_BLACKLIST and len(text) > 10:
                        full_url = href if href.startswith("http") else f"https://www.sis.itu.edu.tr/TR/duyurular/{href}"
                        if not any(a["title"] == text for a in announcements):
                            announcements.append({"title": text, "url": full_url})

            latest_3 = announcements[:3]
            
            for item in announcements:
                if any(kw in item["title"].lower() for kw in ["7592", "af", "geçici 85"]):
                    try:
                        detail_resp = requests.get(item["url"], headers=headers, timeout=5)
                        detail_resp.encoding = "utf-8"
                        detail_soup = BeautifulSoup(detail_resp.text, "html.parser")
                        detail_content += f"\n--- DUYURU: {item['title']} ---\n" + detail_soup.get_text()
                    except:
                        pass

            if not detail_content:
                detail_content = soup.get_text()[:3000]

            prompt = f"""
            İTÜ Öğrenci İşleri duyuru metinleri:
            {detail_content[:3000]}

            GÖREV:
            7592 Sayılı Öğrenci Affı başvurularının BAŞLAYIP BAŞLAMADIĞINI belirle.
            
            ÜSLUP VE KİŞİLİK (ÇOK ÖNEMLİ):
            - Çok samimi, esprili ve sokak ağzıyla eğlenceli bir Türkçe kullan.
            - KESİNLİKLE "kanka", "kankam", "dostum", "bro" gibi kelimeleri KULLANMA. "Kanki" diyebilirsin ara sıra.
            - Eğer BAŞLAMADIYSA: Abartılı ve komik yeminler et.
            - Eğer BAŞLADIYSA: Müjde verir gibi, aşırı heyecanlı ve panik/sevinç havasında yaz!
            - Yalnızca tek cümlelik, vurucu ve komik bir mesaj yaz.

            Format:
            {{
                "durum": "BAŞLADI" veya "BAŞLAMADI",
                "ozet": "Yapay zekanın ürettiği esprili tek cümle"
            }}
            """

            try:
                print(f"--> [API İSTEĞİ] {MODEL_NAME} deneniyor...")
                ai_response = client.models.generate_content(
                    model=MODEL_NAME,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json"
                    )
                )
                result = json.loads(ai_response.text)
                is_started = (result.get("durum") == "BAŞLADI")
                
                site_state["started"] = is_started
                site_state["message"] = result.get("ozet", "Af başvurusu henüz başlamamış.")
                site_state["short_status"] = "Af başladı!" if is_started else "Baktık, daha yok"
                print(f"--> [BAŞARILI] Yanıt {MODEL_NAME} üzerinden alındı.")

            except Exception as err:
                error_msg = f"[{MODEL_NAME}]: {str(err)}"
                print(f"--> [HATA] {error_msg}")
                site_state["started"] = False
                site_state["message"] = f"API Hatası Alındı -> {error_msg}"
                site_state["short_status"] = "API Hatası"

            site_state["last_3"] = latest_3
            
            turkey_tz = pytz.timezone("Europe/Istanbul")
            site_state["last_checked"] = datetime.now(turkey_tz).strftime("%H:%M:%S")

    except Exception as e:
        print(f"--> [KRİTİK HATA] {e}")
        site_state["message"] = f"Sorgulama hatası: {str(e)}"
        site_state["short_status"] = "Hata oluştu"

@app.route("/status")
def get_status():
    current_time = time.time()
    elapsed = current_time - last_request_timestamp
    remaining = max(0, int(COOLDOWN_SECONDS - elapsed))
    
    user_id = session.get("user_id")
    is_same_user = (user_id == last_request_user) if last_request_user else False

    return jsonify({
        "locked": remaining > 0,
        "remaining": remaining,
        "is_same_user": is_same_user
    })

@app.route("/")
def home():
    if "user_id" not in session:
        session["user_id"] = str(uuid.uuid4())
    return render_template("index.html", state=site_state)

@app.route("/check", methods=["POST"])
def manual_check():
    global last_request_timestamp, last_request_user
    
    if "user_id" not in session:
        session["user_id"] = str(uuid.uuid4())

    current_time = time.time()
    elapsed_time = current_time - last_request_timestamp
    
    if elapsed_time >= COOLDOWN_SECONDS:
        last_request_timestamp = current_time
        last_request_user = session["user_id"]
        analyze_announcements_with_ai()
        
    return jsonify(site_state)

@app.route("/sitemap.xml")
def sitemap():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url>
        <loc>https://itu-af-takibi.onrender.com/</loc>
        <changefreq>daily</changefreq>
        <priority>1.0</priority>
      </url>
    </urlset>"""
    return xml, 200, {'Content-Type': 'application/xml'}

if __name__ == "__main__":
    app.run(debug=True, port=5000)