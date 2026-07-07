import logging
import subprocess
import requests
import time
from flask import Flask, request, Response, stream_with_context
from ytmusicapi import YTMusic
import yt_dlp

app = Flask(__name__)
app.config['audio_cache'] = {}
logging.basicConfig(level=logging.INFO)

# Cache with timestamp for auto-expiration
audio_cache = {}  # {video_id: {'url': str, 'timestamp': float}}

# Initialize YouTube Music API
ytmusic = YTMusic()

def search_with_ytmusic(query):
    """Search for song link via YouTube Music"""
    try:
        logging.info(f"🔍 Searching: {query}")
        results = ytmusic.search(query, filter='songs')
        if results:
            video_id = results[0].get('videoId')
            title = results[0].get('title')
            if video_id:
                link = f"https://www.youtube.com/watch?v={video_id}"
                logging.info(f"✅ Found: {title} ({link})")
                return link
        
        # Fallback: Search regular videos if no songs found
        results = ytmusic.search(query, filter='videos')
        if results:
            video_id = results[0].get('videoId')
            if video_id: return f"https://www.youtube.com/watch?v={video_id}"
            
    except Exception as e:
        logging.error(f"❌ Search error: {e}")
    return None

def get_audio_url_with_ytdlp(youtube_url):
    """Get direct audio URL from YouTube using yt-dlp"""
    try:
        ydl_opts = {
            'format': 'bestaudio/best',
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'socket_timeout': 15,
        }
        
        logging.info(f"🔄 Getting audio URL from: {youtube_url}")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(youtube_url, download=False)
            
            # Get direct URL
            audio_url = info.get('url')
            if not audio_url and 'formats' in info:
                audio_formats = [f for f in info['formats'] if f.get('acodec') != 'none']
                if audio_formats:
                    best = max(audio_formats, key=lambda f: f.get('abr', 0))
                    audio_url = best['url']
            
            if audio_url:
                logging.info(f"✅ Got audio URL successfully")
                # Validate URL with HEAD request
                try:
                    resp = requests.head(audio_url, timeout=5)
                    if resp.status_code == 200:
                        logging.info(f"✅ Audio URL validated (200 OK)")
                    else:
                        logging.warning(f"⚠️ Audio URL returned status: {resp.status_code}")
                except Exception as e:
                    logging.warning(f"⚠️ Could not validate URL: {e}")
            return audio_url
            
    except Exception as e:
        logging.error(f"❌ yt-dlp error: {e}")
        return None

@app.route('/')
def home():
    return "Xiaozhi Music Server (Ultimate Edition) is Running!"

@app.route('/health')
def health():
    """Health check endpoint - ESP32 can ping to ensure server is ready"""
    return {"status": "ok", "timestamp": time.time()}

@app.route('/stream')
def stream_music():
    query = request.args.get('q')
    if not query: return "Missing song name", 400
    
    youtube_link = query
    
    # 1. Search
    if not query.startswith("http"):
         found_link = search_with_ytmusic(query)
         if found_link: 
             youtube_link = found_link
         else: 
             return "Song not found.", 404

    # 2. Get audio URL using yt-dlp
    audio_url = get_audio_url_with_ytdlp(youtube_link)
    
    if not audio_url: 
        return "Could not get audio URL.", 404

    logging.info(f"🎶 Streaming from: {audio_url}")

    # 3. Convert to PCM
    ffmpeg_cmd = [
        'ffmpeg', '-re', '-i', audio_url, 
        '-f', 's16le', '-acodec', 'pcm_s16le', 
        '-ar', '16000', '-ac', '1', '-vn', '-'
    ]
    
    def generate():
        process = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            while True:
                data = process.stdout.read(4096)
                if not data: break
                yield data
        finally:
            process.kill()

    return Response(stream_with_context(generate()), mimetype='audio/pcm')

@app.route('/stream_pcm')
def stream_pcm():
    """Endpoint for ESP32 - returns JSON with relative audio_url"""
    song = request.args.get('song', '')
    artist = request.args.get('artist', '')
    
    # Combine song and artist
    query = f"{song} {artist}".strip()
    if not query:
        return {"error": "Missing song name"}, 400
    
    youtube_link = query
    title = song
    artist_name = artist
    video_id = None  # Initialize video_id
    
    # 1. Search
    if not query.startswith("http"):
         results = ytmusic.search(query, filter='songs')
         if results:
             video_id = results[0].get('videoId')
             title = results[0].get('title')
             artist_name = results[0].get('artists', [{}])[0].get('name', artist) if results[0].get('artists') else artist
             
             if video_id:
                 youtube_link = f"https://www.youtube.com/watch?v={video_id}"
                 logging.info(f"✅ Found: {title} - {artist_name} ({youtube_link})")
             else:
                 return {"error": "Song not found"}, 404
         else:
             return {"error": "Song not found"}, 404

    # 2. Check cache first (expires after 5 minutes)
    cache_key = video_id if video_id else query
    current_time = time.time()
    
    if cache_key in audio_cache:
        cached_data = audio_cache[cache_key]
        age = current_time - cached_data['timestamp']
        if age < 300:  # 5 minutes
            audio_url = cached_data['url']
            logging.info(f"♻️ Using cache (age: {age:.0f}s): {cache_key}")
        else:
            logging.info(f"🗑️ Cache expired (age: {age:.0f}s), getting new URL")
            del audio_cache[cache_key]
            audio_url = None
    else:
        audio_url = None
    
    # Get new audio URL if not cached
    if not audio_url:
        audio_url = get_audio_url_with_ytdlp(youtube_link)
        
        if not audio_url: 
            return {"error": "Could not get audio URL"}, 404
        
        # Cache with timestamp
        audio_cache[cache_key] = {
            'url': audio_url,
            'timestamp': current_time
        }
        logging.info(f"💾 Cached audio URL for: {cache_key}")

    # 3. Return JSON with RELATIVE audio_url for ESP32 to call proxy endpoint
    return {
        "success": True,
        "title": title,
        "artist": artist_name,
        "audio_url": f"/stream_audio?v={video_id if video_id else 'direct'}",
        "format": "audio/webm",
        "sample_rate": 16000
    }

@app.route('/stream_audio')
def stream_audio():
    """Proxy endpoint - convert WebM to MP3 and stream to ESP32"""
    video_id = request.args.get('v', 'direct')
    
    # Add longer delay to give ESP32 time to cleanup after previous song
    # ESP32 needs time to:
    # - Stop playback thread
    # - Clear audio buffer
    # - Reset MP3 decoder
    # - Disable/enable audio output
    time.sleep(2.0)  # Increased from 0.5s to 2s
    
    # Get audio URL from cache
    if video_id not in audio_cache:
        logging.error(f"❌ Audio URL not found in cache for video_id: {video_id}")
        logging.info(f"📦 Available cache keys: {list(audio_cache.keys())}")
        return "Audio URL not found in cache", 404
    
    cached_data = audio_cache[video_id]
    audio_url = cached_data['url']
    age = time.time() - cached_data['timestamp']
    
    logging.info(f"🎵 Streaming MP3 for: {video_id} (cache age: {age:.0f}s)")
    
    # Convert WebM/Opus to MP3 realtime with FFmpeg
    # ESP32 will decode MP3 → PCM using MP3Decode()
    # Using 44100Hz stereo - standard MP3 format compatible with ESP32 decoder
    ffmpeg_cmd = [
        'ffmpeg',
        '-reconnect', '1',         # Auto reconnect if connection lost
        '-reconnect_streamed', '1',
        '-reconnect_delay_max', '5',
        '-i', audio_url,           # Input from YouTube
        '-f', 'mp3',               # Output format MP3
        '-acodec', 'libmp3lame',   # MP3 encoder
        '-b:a', '128k',            # Bitrate 128kbps
        '-ar', '44100',            # Force 44100Hz (matches ESP32 music mode)
        '-ac', '2',                # Stereo
        '-'                        # Output to stdout
    ]
    
    def generate():
        process = None
        try:
            process = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            chunk_count = 0
            total_bytes = 0
            
            # Wait for first chunk to be ready before streaming
            first_chunk = process.stdout.read(8192)
            if first_chunk:
                logging.info(f"📤 First chunk ready: {len(first_chunk)} bytes, header: {first_chunk[:16].hex()}")
                yield first_chunk
                chunk_count = 1
                total_bytes = len(first_chunk)
            
            while True:
                data = process.stdout.read(8192)
                if not data:
                    break
                chunk_count += 1
                total_bytes += len(data)
                
                if chunk_count % 50 == 0:  # Log every ~400KB
                    logging.info(f"📤 Streamed {total_bytes} bytes")
                yield data
                
            logging.info(f"✅ Stream completed: {total_bytes} bytes total")
            
        except Exception as e:
            logging.error(f"❌ Stream error: {e}")
            if process:
                stderr = process.stderr.read().decode('utf-8', errors='ignore')
                logging.error(f"FFmpeg stderr: {stderr}")
        finally:
            if process:
                process.kill()
                process.wait()
    
    return Response(stream_with_context(generate()), mimetype='audio/mpeg', headers={
        'Content-Type': 'audio/mpeg',
        'Accept-Ranges': 'bytes',
        'Cache-Control': 'no-cache'
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=7071)
