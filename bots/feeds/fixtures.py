"""Small feed documents the parser and poll tests share."""

RSS = b"""<?xml version="1.0"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><title>Example Blog</title>
<item><guid>g-2</guid><title>Second &amp; newer</title><link>https://example.com/2</link>
<description>&lt;p&gt;Hello &lt;b&gt;world&lt;/b&gt; about rust&lt;/p&gt;</description><pubDate>Tue, 06 Oct 2026 10:00:00 +0000</pubDate></item>
<item><guid>g-1</guid><title>First</title><link>https://example.com/1</link>
<description>Old post about python</description><pubDate>Mon, 05 Oct 2026 10:00:00 +0000</pubDate></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/"><title>A Channel</title>
<entry><id>yt:video:b</id><title>Video B</title><link rel="alternate" href="https://youtube.com/watch?v=b"/>
<published>2026-10-06T10:00:00+00:00</published><media:group><media:description>About B</media:description></media:group></entry>
<entry><id>yt:video:a</id><title>Video A</title><link rel="self" href="https://x.example/self"/><link href="https://youtube.com/watch?v=a"/>
<updated>2026-10-05T10:00:00Z</updated><summary>About A</summary></entry>
</feed>"""

RDF = b"""<?xml version="1.0"?><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/">
<channel><title>Old Style</title></channel><item><title>One</title><link>https://old.example/1</link></item></rdf:RDF>"""

BOMB = b"""<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaaaaaaaa">]><rss version="2.0"><channel><title>&a;</title></channel></rss>"""


def rss_with(*items, title="Example Blog"):
    body = "".join(f"<item><guid>{g}</guid><title>{t}</title><link>https://example.com/{g}</link><description>{d}</description></item>" for g, t, d in items)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>{title}</title>{body}</channel></rss>'.encode()
