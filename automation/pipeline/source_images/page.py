"""Extract article photograph candidates from HTML metadata and figures."""
import json
from html.parser import HTMLParser
from urllib.parse import urljoin


class ArticlePage(HTMLParser):
    def __init__(self, url: str):
        super().__init__(convert_charrefs=True)
        self.url = url
        self.candidates = []
        self.title = ""
        self._title = False
        self._json = None
        self._figure = None
        self._caption = False

    def add(self, url, alt="", caption="", credit="", priority=2):
        if not isinstance(url, str) or not url.strip():
            return
        self.candidates.append({"url": urljoin(self.url, url.strip()), "alt": alt or "",
                                "caption": caption or "", "credit": credit or "",
                                "source": self.url, "priority": priority})

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title":
            self._title = True
        elif tag == "script" and a.get("type") == "application/ld+json":
            self._json = []
        elif tag == "meta":
            key = a.get("property", a.get("name", "")).lower()
            if key in ("og:image", "og:image:url", "twitter:image"):
                self.add(a.get("content"), priority=0)
            elif key == "og:title":
                self.title = a.get("content", self.title)
            elif key == "og:image:alt" and self.candidates:
                self.candidates[-1]["alt"] = a.get("content", "")
        elif tag == "figure":
            self._figure = []
        elif tag == "figcaption":
            self._caption = True
        elif tag == "img" and self._figure is not None:
            if any(a.get(k, "").isdigit() and int(a[k]) < 250 for k in ("width", "height")):
                return
            url = a.get("data-src") or a.get("src")
            self.add(url, alt=a.get("alt", ""))
            if url:
                self._figure.append(self.candidates[-1])

    def handle_data(self, text):
        if self._json is not None:
            self._json.append(text)
        elif self._title:
            self.title += text
        elif self._caption and self._figure:
            for candidate in self._figure:
                candidate["caption"] += text

    def handle_endtag(self, tag):
        if tag == "title":
            self._title = False
        elif tag == "script" and self._json is not None:
            try:
                self._structured(json.loads("".join(self._json)))
            except (ValueError, TypeError):
                pass
            self._json = None
        elif tag == "figcaption":
            self._caption = False
        elif tag == "figure":
            self._figure = None

    def _structured(self, obj):
        if isinstance(obj, list):
            for item in obj:
                self._structured(item)
        elif isinstance(obj, dict):
            kind = obj.get("@type", [])
            kinds = [kind] if isinstance(kind, str) else kind
            if any(k in kinds for k in ("NewsArticle", "Article", "BlogPosting")):
                images = obj.get("image", [])
                images = images if isinstance(images, list) else [images]
                for im in images:
                    if isinstance(im, str):
                        self.add(im, priority=0)
                    elif isinstance(im, dict):
                        self._image(im)
            elif "ImageObject" in kinds:
                self._image(obj)
            for key, value in obj.items():
                if key != "image" and isinstance(value, (dict, list)):
                    self._structured(value)

    def _image(self, obj):
        creator = obj.get("creator") or {}
        credit = obj.get("creditText") or (creator.get("name") if isinstance(creator, dict) else creator)
        self.add(obj.get("contentUrl") or obj.get("url"),
                 caption=obj.get("caption", ""), credit=credit, priority=0)

    def images(self):
        unique = {}
        for candidate in sorted(self.candidates, key=lambda c: c["priority"]):
            url = candidate["url"]
            if url in unique:
                for key in ("caption", "credit", "alt"):
                    unique[url][key] = unique[url][key] or candidate[key]
            else:
                unique[url] = {**candidate, "source_title": self.title.strip()}
        return list(unique.values())
