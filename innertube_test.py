"""Teste: cliente ANDROID do Innertube passa no bot check de runner datacenter?"""
import json
import re
import requests

VID = "8uwd5O8Q9l4"
UA = "com.google.android.youtube/20.10.38 (Linux; U; Android 11) gzip"


def main():
    page = requests.get(f"https://www.youtube.com/watch?v={VID}",
                        headers={"User-Agent": "Mozilla/5.0"}, timeout=20).text
    key = re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', page).group(1)
    payload = {
        "context": {"client": {
            "clientName": "ANDROID", "clientVersion": "20.10.38",
            "androidSdkVersion": 30, "osName": "Android", "osVersion": "11",
            "userAgent": UA, "hl": "pt",
        }},
        "videoId": VID, "contentCheckOk": True, "racyCheckOk": True,
    }
    headers = {"Content-Type": "application/json", "User-Agent": UA,
               "X-YouTube-Client-Name": "3", "X-YouTube-Client-Version": "20.10.38",
               "Origin": "https://www.youtube.com"}
    r = requests.post(f"https://www.youtube.com/youtubei/v1/player?key={key}&prettyPrint=false",
                      json=payload, headers=headers, timeout=20)
    d = r.json()
    print("HTTP", r.status_code)
    print("playability", json.dumps(d.get("playabilityStatus", {}), ensure_ascii=False)[:600])
    print("viewCount", d.get("videoDetails", {}).get("viewCount"))
    print("streaming formats", len(d.get("streamingData", {}).get("adaptiveFormats", [])))
    ip = requests.get("https://api.ipify.org?format=json", timeout=10).json()
    print("runner ip", ip)


if __name__ == "__main__":
    main()
