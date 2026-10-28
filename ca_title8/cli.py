import argparse
import importlib
import logging
import sys
from pathlib import Path

from .crawler import crawl
from .http import Cache, HttpClient, atomic_json


def positive(value):
    result = float(value)
    if result <= 0 or result == float("inf") or result != result:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Scrape California CCR Title 8 into JSON. Default: Cornell's full-title snapshot.")
    parser.add_argument("--source", choices=["cornell", "dir"], default="cornell",
                        help="cornell: all three divisions; dir: official DIR Division 1 collection")
    parser.add_argument("--output", type=Path, default=Path("data/title8.json"))
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache/title8"))
    parser.add_argument("--delay", type=positive, default=1.0,
                        help="minimum seconds between requests; robots.txt may raise it")
    parser.add_argument("--timeout", type=positive, default=30.0)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--max-pages", type=int, help="stop after this many pages; output is marked partial")
    parser.add_argument("--start-url", action="append", help="crawl a source subtree or section; repeatable")
    parser.add_argument("--refresh", action="store_true", help="redownload pages instead of reusing the cache")
    parser.add_argument("--include-html", action="store_true", help="retain the regulation HTML alongside text")
    parser.add_argument("--user-agent", help="custom User-Agent, optionally including your contact information")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    if args.retries < 0:
        parser.error("--retries must be nonnegative")
    if args.max_pages is not None and args.max_pages <= 0:
        parser.error("--max-pages must be positive")
    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    source = importlib.import_module(f"ca_title8.sources.{args.source}")
    logging.warning("Source coverage: %s", source.SOURCE_SCOPE)
    logging.warning("Source currency: %s", source.FRESHNESS_NOTE)
    options = {"delay": args.delay, "timeout": args.timeout,
               "retries": args.retries, "refresh": args.refresh}
    if args.user_agent:
        options["user_agent"] = args.user_agent
    client = HttpClient(source, Cache(args.cache_dir / args.source), **options)
    try:
        result = crawl(source, client, start_urls=args.start_url,
                       max_pages=args.max_pages, include_html=args.include_html,
                       checkpoint=lambda value: atomic_json(args.output, value))
        atomic_json(args.output, result)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")
    meta = result["metadata"]
    print(f"Saved {meta['entry_count']} entries, {meta['index_count']} indexes, "
          f"{meta['error_count']} errors to {args.output}")
    print(f"Crawl complete: {meta['crawl_complete']}; full source tree: {meta['covers_full_source_tree']}")
    if meta["interrupted"]:
        return 130
    if meta["error_count"]:
        return 1
    if not meta["crawl_complete"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
