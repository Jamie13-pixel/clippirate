import os
import time
import requests


def _headers():
    return {"Authorization": os.environ["PEXELS_API_KEY"]}


def _query_pexels(query, count, orientation=None):
    url = "https://api.pexels.com/videos/search"
    params = {
        "query": query,
        "orientation": orientation,
        "per_page": count,
    }
    response = requests.get(url, headers=_headers(), params=params, timeout=15)
    response.raise_for_status()
    return response.json().get("videos", [])


def search_video_clips(query, count=1, aspect_ratio="4:3"):
    query = (query or "").strip() or "abstract background"

    try:
        orientation = "portrait" if aspect_ratio == "9:16" else "landscape"
        videos = _query_pexels(query, count, orientation)
    except Exception as e:
        print("[stock_footage] Pexels search failed for '" + query + "': " + str(e))
        videos = []

    if not videos:
        print("[stock_footage] No results for '" + query + "', falling back to generic footage")
        try:
            videos = _query_pexels("abstract background", count, orientation)
        except Exception as e:
            print("[stock_footage] Fallback search also failed: " + str(e))
            videos = []

    clip_urls = []
    for video in videos:
        files = video["video_files"]
        pool_sorted = sorted(files, key=lambda f: f.get("width", 0))
        chosen = pool_sorted[0]  # smallest file, keeps memory use low
        clip_urls.append(chosen["link"])

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