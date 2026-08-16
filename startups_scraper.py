"""
Startup & Founder Contact Scraper.

Sources:
  - Product Hunt RSS (Today's newest product launches & makers)
  - Hacker News "Show HN" API (New founder launches & websites)
  - Startup Website Contact Extractor (Extracts public emails / founder patterns)
"""

import re
import logging
import requests
import xml.etree.ElementTree as ET
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) StartupScraper/1.0"}


def extract_founder_contact(website_url: str) -> dict:
    """
    Extracts public contact email from a startup's website or generates standard founder emails.
    """
    if not website_url:
        return {"email": "contact@startup.com", "domain": "N/A"}

    parsed = urlparse(website_url)
    domain = parsed.netloc.replace("www.", "")
    if not domain or "." not in domain:
        return {"email": f"hello@{website_url}", "domain": website_url}

    found_email = None
    try:
        resp = requests.get(website_url, headers=HEADERS, timeout=6)
        if resp.status_code == 200:
            emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', resp.text)
            valid = [
                e for e in emails
                if not any(e.lower().endswith(ext) for ext in ['.png', '.jpg', '.svg', '.gif', '.css', '.js', '.webp'])
                and not any(ignore in e.lower() for ignore in ['w3.org', 'sentry', 'schema', 'example', 'domain', 'sentry.io', 'format'])
            ]
            if valid:
                found_email = valid[0]
    except Exception:
        pass

    if not found_email:
        found_email = f"hello@{domain} / founder@{domain}"

    return {
        "email": found_email,
        "domain": domain
    }


def fetch_product_hunt_startups():
    """Fetches today's top product launches from Product Hunt."""
    try:
        resp = requests.get("https://www.producthunt.com/feed", headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return []

        root = ET.fromstring(resp.text)
        items = []
        for entry in root.findall("{http://www.w3.org/2005/Atom}entry")[:10]:
            title_node = entry.find("{http://www.w3.org/2005/Atom}title")
            title = title_node.text if title_node is not None else "Untitled Startup"
            
            link_node = entry.find("{http://www.w3.org/2005/Atom}link")
            link = link_node.attrib.get("href") if link_node is not None else ""
            
            content_node = entry.find("{http://www.w3.org/2005/Atom}content")
            summary = content_node.text if content_node is not None else ""

            # Clean pitch text
            clean_pitch = re.sub(r'<[^>]+>', '', summary).strip()[:180] if summary else "New Product Launch"
            
            # Extract target website link if available inside content
            website_match = re.search(r'href="(https?://(?!www\.producthunt\.com)[^"]+)"', summary or "")
            target_url = website_match.group(1) if website_match else link

            items.append({
                "name": title,
                "pitch": clean_pitch,
                "link": target_url or link,
                "ph_link": link,
                "founder": "Product Hunt Maker",
                "source": "Product Hunt"
            })
        return items
    except Exception as e:
        logger.warning("Product Hunt fetch failed: %s", e)
        return []


def fetch_hacker_news_startups():
    """Fetches newly launched products & startups from Hacker News 'Show HN'."""
    try:
        resp = requests.get("https://hacker-news.firebaseio.com/v0/showstories.json", headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return []
        
        story_ids = resp.json()[:10]
        items = []
        for sid in story_ids:
            sresp = requests.get(f"https://hacker-news.firebaseio.com/v0/item/{sid}.json", headers=HEADERS, timeout=5)
            if sresp.status_code == 200:
                data = sresp.json()
                if data and data.get("url"):
                    name = data.get("title", "").replace("Show HN: ", "").strip()
                    items.append({
                        "name": name,
                        "pitch": f"Launched by @{data.get('by')} on Hacker News",
                        "link": data.get("url"),
                        "founder": f"@{data.get('by')} (HN Founder)",
                        "source": "Show HN"
                    })
        return items
    except Exception as e:
        logger.warning("Hacker News fetch failed: %s", e)
        return []


def get_new_startups_and_founders(limit: int = 15):
    """
    Fetches startups concurrently from Product Hunt and Hacker News,
    extracts founder contact emails, and returns a unified list.
    """
    startups = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        f_ph = executor.submit(fetch_product_hunt_startups)
        f_hn = executor.submit(fetch_hacker_news_startups)
        
        try:
            startups.extend(f_ph.result(timeout=12))
        except Exception:
            pass
        try:
            startups.extend(f_hn.result(timeout=12))
        except Exception:
            pass

    # Deduplicate startups by link or name
    seen = set()
    deduped = []
    for s in startups:
        key = s["name"].lower().strip()
        if key not in seen:
            seen.add(key)
            deduped.append(s)

    # Concurrently enrich top startups with contact emails
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_startup = {
            executor.submit(extract_founder_contact, s["link"]): s
            for s in deduped[:limit]
        }
        for future in as_completed(future_to_startup):
            s = future_to_startup[future]
            try:
                info = future.result()
                s["email"] = info["email"]
                s["domain"] = info["domain"]
            except Exception:
                s["email"] = f"hello@{urlparse(s['link']).netloc.replace('www.', '')}"

    return deduped[:limit]
