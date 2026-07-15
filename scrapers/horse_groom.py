"""Horse & Groom scraper — no automated data source available.

Investigation (task t_c9449876) found:
- Instagram @thehorseandgroomwindsor: 14 posts, only 2 music-related in 2 years
- Sunday Afternoon Song Circle: defunct since 2017
- No web events page or Lemonrock listing
- Songkick: 0 upcoming concerts (last gig 2015)
- Website: simple WordPress site, no events/music page

The venue currently has no regular live music programme.
This scraper exists as a placeholder so the dispatch in generator.py
explicitly handles this venue rather than falling through to the
"no scraper for slug" warning.

If the venue starts publishing events again (new Instagram activity,
website events page, Lemonrock listing), update this scraper to
actually fetch and parse them.
"""

import sys


def scrape(url=None) -> list[dict]:
    """Scrape Horse & Groom events — always returns empty list.

    Args:
        url: Ignored. Kept for API compatibility with generator dispatch.

    Returns:
        Always [] — no scrapeable data source exists.
    """
    print("  [web] The Horse & Groom: no automated source (see scrapers/horse_groom.py docs)", file=sys.stderr)
    return []