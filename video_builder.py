import os, requests, textwrap
from moviepy.editor import AudioFileClip, ColorClip, CompositeVideoClip, VideoFileClip, ImageClip, ImageSequenceClip
from PIL import Image, ImageDraw, ImageFont
import tempfile

def make_text_image(text, W, fontsize=65):
    # Create caption image with PIL, no ImageMagick needed
    try:
        img = Image.new('RGBA', (W-80, 300), (0,0,0,0))
        draw = ImageDraw.Draw(img)
        # try load bold font
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", fontsize)
        except:
            font = ImageFont.load_default()
        wrapped = textwrap.fill(text, width=22)
        # get bbox
        bbox = draw.multiline_textbbox((0,0), wrapped, font=font, stroke_width=4)
        tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
        img = Image.new('RGBA', (tw+20, th+20), (0,0,0,0))
        draw = ImageDraw.Draw(img)
        draw.multiline_text((10,10), wrapped, font=font, fill=(255,255,255,255), stroke_width=4, stroke_fill=(0,0,0,255), align="center", spacing=4)
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
        img.save(tmp)
        return tmp, th
    except Exception as e:
        print(f"text img err {e}")
        return None, 0

def build_video(audio_path=None, audio_file=None, output_path=None, output_file=None, topic="", script_text="", timeline=None, aspect_ratio="9:16", **kwargs):
    audio_src = audio_file or audio_path
    out = output_file or output_path or "/tmp/output.mp4"
    key = (kwargs.get("pixabay_key") or kwargs.get("pexels_key") or os.getenv("PIXABAY_API_KEY","")).strip()
    print(f"Pixabay build: dur key={'YES' if key else 'NO'} topic={topic} key_len={len(key)}")

    audio = AudioFileClip(audio_src)
    duration = audio.duration
    W,H = (1080,1920) if aspect_ratio=="9:16" else (1920,1080)

    bg = None
    # Try video
    for q in [topic.split()[0] if topic else "football", "football soccer", "soccer"]:
        try:
            url = f"https://pixabay.com/api/videos/?key={key}&q={q}&per_page=3&safesearch=true"
            print(f"Trying Pixabay video: {q} | Status...")
            r = requests.get(url, timeout=15)
            print(f"Status {r.status_code} -> {r.text[:200]}")
            if r.status_code==200 and r.json().get("hits"):
                vurl = r.json()["hits"][0]["videos"]["large"]["url"]
                tmp = "/tmp/pb.mp4"
                with open(tmp,"wb") as f:
                    f.write(requests.get(vurl, timeout=20).content)
                bg = VideoFileClip(tmp).resize((W,H)).set_duration(duration)
                print("VIDEO FOUND!")
                break
        except Exception as e:
            print(f"video err {e}")

    if not bg:
        for q in ["football", "soccer stadium"]:
            try:
                url = f"https://pixabay.com/api/?key={key}&q={q}&image_type=photo&per_page=3&orientation=vertical"
                print(f"Trying Pixabay image: {q}")
                r = requests.get(url, timeout=15)
                print(f"Image status {r.status_code} hits {len(r.json().get('hits',[])) if r.status_code==200 else r.text[:100]}")
                if r.status_code==200 and r.json().get("hits"):
                    iurl = r.json()["hits"][0]["largeImageURL"]
                    tmp = "/tmp/pb.jpg"
                    with open(tmp,"wb") as f:
                        f.write(requests.get(iurl, timeout=15).content)
                    bg = ImageClip(tmp, duration=duration).resize((W,H))
                    print("IMAGE FOUND!")
                    break
            except Exception as e:
                print(f"img err {e}")

    if not bg:
        print("Pixabay failed all -> fallback COLOR (KEY IS WRONG!)")
        bg = ColorClip(size=(W,H), color=(25,25,80), duration=duration)

    clips = [bg, ColorClip(size=(W,H), color=(0,0,0), duration=duration).set_opacity(0.35)]

    if timeline:
        for entry in timeline:
            try:
                txt = entry.get("text","")
                if not txt: continue
                start = float(entry.get("start",0))
                end = float(entry.get("end", start+2))
                img_path, th = make_text_image(txt, W)
                if img_path:
                    ic = ImageClip(img_path, duration=end-start).set_start(start).set_position(('center', H*0.72))
                    clips.append(ic)
            except Exception as e:
                print(f"cap err {e}")

    final = CompositeVideoClip(clips).set_audio(audio).set_duration(duration)
    final.write_videofile(out, fps=24, codec='libx264', audio_codec='aac', logger=None)
    print(f"Saved {out}")
    audio.close()
    return out