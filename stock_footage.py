import os
import time
import requests

def _query_pixabay(query, count, orientation=None):
    api_key = os.getenv("PIXABAY_API_KEY", "")
    if not api_key:
        print("[stock_footage] ERROR: PIXABAY_API_KEY is empty!")
        return []

    # Correct Pixabay endpoint
    url = "https://pixabay.com/api/videos/"
    params = {
        "key": api_key, # <-- key in params, not header
        "q": query,
        "per_page": count,
        "safesearch": "true",
        "order": "popular"
    }

    print(f"[stock_footage] Searching Pixabay: q='{query}'")

    response = requests.get(url, params=params, timeout=15)
    print(f"[stock_footage] Pixabay status: {response.status_code}")

    if response.status_code!= 200:
        print(f"[stock_footage] Error body: {response.text[:300]}")
        return []

    data = response.json()
    hits = data.get("hits", [])
    print(f"[stock_footage] Found {len(hits)} hits")

    # Convert Pixabay format to your expected format
    videos = []
    for hit in hits:
        # Pixabay has videos->large->url
        vids = hit.get("videos", {})
        large = vids.get("large", {})
        if large.get("url"):
            videos.append({
                "video_files": [
                    {"width": 1280, "link": large["url"]},
                    {"width": 640, "link": vids.get("medium", {}).get("url") or large["url"]}
                ]
            })
    return videos

def search_video_clips(query, count=1, aspect_ratio="4:3"):
    query = (query or "").strip() or "abstract background"

    try:
        orientation = "portrait" if aspect_ratio == "9:16" else "landscape"
        videos = _query_pixabay(query, count, orientation)
    except Exception as e:
        print("[stock_footage] Pixabay search failed for '" + query + "': " + str(e))
        videos = []

    if not videos:
        print("[stock_footage] No results for '" + query + "', falling back to generic footage")
        try:
            videos = _query_pixabay("abstract background", count, orientation)
        except Exception as e:
            print("[stock_footage] Fallback search also failed: " + str(e))
            videos = []

    clip_urls = []
    for video in videos:
        files = video["video_files"]
        pool_sorted = sorted(
            files,
            key=lambda f: f.get("width", 0),
            reverse=True,
        )

        chosen = pool_sorted[0]
        clip_urls.append(chosen["link"])

    print(f"[stock_footage] Returning {len(clip_urls)} clip URLs")
    return clip_urls

def download_clip(url, dest_path, retries=3, timeout=60):
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(url, stream=True, timeout=timeout)
            response.raise_for_status()
            with open(dest_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=1 << 16):
                    f.write(chunk)
            return dest_path
        except Exception as e:
            last_error = e
            print(
                "[stock_footage] Download attempt " + str(attempt) + "/" + str(retries)
                + " failed for " + url + ": " + str(e)
            )
            if os.path.exists(dest_path):
                try:
                    os.remove(dest_path)
                except Exception:
                    pass
            time.sleep(1.5 * attempt)

    raise ConnectionError(
        "Failed to download " + url + " after " + str(retries) + " attempts: " + str(last_error)
    )