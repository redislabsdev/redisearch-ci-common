#!/usr/bin/env python3
"""
Link checker for Markdown files.
Validates all links in .md files, including anchor links.
"""

import argparse
import json
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Tuple, Set, Dict, Any
from urllib.parse import urlparse, urlunparse

import requests
from bs4 import BeautifulSoup


class LinkChecker:
    def __init__(self, config: Dict[str, Any] = None, verbose: bool = False):
        if config is None:
            config = {}

        self.timeout = config.get('timeout', 10)
        self.max_workers = config.get('max_workers', 10)
        self.delay = config.get('delay', 0.1)
        self.exclude_urls = set(config.get('exclude_urls', []))
        self.exclude_patterns = [re.compile(pattern) for pattern in config.get('exclude_link_patterns', [])]
        self.exclude_dirs = set(config.get('exclude_directories', [
            'bin', 'deps', 'tests', 'scripts', 'venv', '.github', '.git', '__pycache__', '.pytest_cache'
        ]))
        self.verbose = verbose

        self.session = requests.Session()
        user_agent = config.get('user_agent', 'Mozilla/5.0 (compatible; RediSearch-LinkChecker/1.0)')
        self.session.headers.update({'User-Agent': user_agent})
        self.checked_urls: Set[str] = set()
        # Guards checked_urls across the ThreadPoolExecutor workers in
        # check_all_files. The shared requests.Session is only used for
        # concurrent GETs after its headers are set above (thread-safe), so it
        # needs no lock.
        self._lock = threading.Lock()

    def find_markdown_files(self, directory: str) -> List[Path]:
        """Find all Markdown files in the directory, excluding certain subdirectories."""
        path = Path(directory)

        md_files = []
        for md_file in path.rglob("*.md"):
            # Check if any parent directory is in the excluded set
            if any(part in self.exclude_dirs for part in md_file.parts):
                continue
            md_files.append(md_file)

        return md_files

    def extract_links(self, content: str, file_path: Path = None) -> List[Tuple[str, int, str]]:
        """Extract all links from Markdown content with line numbers and types."""
        links = []
        lines = content.split('\n')

        # Regex patterns for different link types
        patterns = [
            r'\[([^\]]*)\]\(([^)]+)\)',  # [text](url)
            r'<(https?://[^>]+)>',       # <url> - only if starts with http
            r'(?:^|\s)(https?://\S+)',   # bare URLs
        ]

        for line_num, line in enumerate(lines, 1):
            for pattern in patterns:
                matches = re.finditer(pattern, line)
                for match in matches:
                    if pattern == patterns[0]:  # [text](url) format
                        url = match.group(2)
                    elif pattern == patterns[1]:  # <url> format
                        url = match.group(1)
                    else:  # bare URL format
                        url = match.group(1)

                    # Skip mailto links
                    if url.startswith('mailto:'):
                        continue

                    # Skip obvious placeholders
                    if url.lower() in {'url', 'link', 'path', 'file', 'example.com', 'domain.com'}:
                        continue

                    # Determine link type and resolve if relative
                    if url.startswith(('http://', 'https://')):
                        link_type = 'absolute'
                        resolved_url = url
                    else:
                        link_type = 'relative'
                        if file_path:
                            # Resolve relative path against the markdown file's directory
                            resolved_url = self._resolve_relative_path(url, file_path)
                        else:
                            resolved_url = url

                    # Skip excluded URLs
                    if self._should_exclude_url(resolved_url):
                        continue

                    links.append((resolved_url, line_num, link_type))

        return links

    def _resolve_relative_path(self, url: str, file_path: Path) -> str:
        """Resolve a relative link against the markdown file's directory.

        The `#fragment` is preserved (re-attached to the resolved path) so the
        anchor can be validated later. A pure same-file anchor (`#section`)
        resolves to the current file so its own headings are checked.
        """
        without_query = url.split('?', 1)[0]
        clean_url, _, fragment = without_query.partition('#')

        file_dir = file_path.parent
        # `#anchor` with no path -> the current file.
        target = clean_url if clean_url else file_path.name
        resolved_path = (file_dir / target).resolve()

        return f"{resolved_path}#{fragment}" if fragment else str(resolved_path)

    def _should_exclude_url(self, url: str) -> bool:
        """Check if URL should be excluded from checking."""
        if url in self.exclude_urls:
            return True

        for pattern in self.exclude_patterns:
            if pattern.match(url):
                return True

        return False

    def check_url_with_anchor(self, url: str, link_type: str = 'absolute') -> Tuple[bool, str]:
        """Check if URL is valid, including anchor verification."""
        with self._lock:
            already_checked = url in self.checked_urls
        if already_checked:
            return True, "Already checked"

        try:
            if link_type == 'relative':
                return self._check_relative_link(url)
            else:
                return self._check_absolute_link(url)

        except Exception as e:
            return False, f"Error: {str(e)}"

    def _check_relative_link(self, file_path: str) -> Tuple[bool, str]:
        """Check that a relative file/dir exists; for `*.md#anchor` links, also
        verify the anchor resolves to a heading or explicit anchor in the target."""
        path_part, _, fragment = file_path.partition('#')
        path = Path(path_part)

        if not path.exists():
            return False, "Path not found"

        if path.is_dir():
            with self._lock:
                self.checked_urls.add(file_path)
            return True, "Directory exists"

        if not path.is_file():
            return False, "Path exists but is neither file nor directory"

        # Anchor validation only for Markdown targets; other file types just
        # need to exist.
        if fragment and path.suffix.lower() in ('.md', '.markdown'):
            if not self._anchor_in_markdown(path, fragment):
                return False, f"Anchor '#{fragment}' not found in {path.name}"
            with self._lock:
                self.checked_urls.add(file_path)
            return True, "File and anchor exist"

        with self._lock:
            self.checked_urls.add(file_path)
        return True, "File exists"

    # Line-number anchors (#L10, #L10-L20) are rendered client-side by the code
    # viewer and never appear as heading ids — skip them rather than false-fail.
    _LINE_ANCHOR_RE = re.compile(r'^L\d+(?:-L\d+)?$', re.IGNORECASE)

    def _anchor_in_markdown(self, path: Path, fragment: str) -> bool:
        """True if `fragment` matches a heading slug or an explicit anchor in the
        target Markdown. Uses GitHub's heading-slug rules and also accepts
        explicit `<a id/name=...>` and `{#custom-id}` anchors. Errs toward True
        (skips validation) for line-number anchors or when the file can't be
        read, and only ever *adds* candidate anchors, so it fails solely on a
        genuinely-absent anchor."""
        if self._LINE_ANCHOR_RE.match(fragment):
            return True
        try:
            text = path.read_text(encoding='utf-8')
        except Exception:
            return True

        target = fragment.lower()
        anchors: Set[str] = set()
        slug_counts: Dict[str, int] = {}
        in_fence = False
        prev = ''
        for line in text.split('\n'):
            stripped = line.strip()
            if stripped.startswith('```') or stripped.startswith('~~~'):
                in_fence = not in_fence
                prev = ''
                continue
            if in_fence:
                prev = stripped
                continue

            # Explicit custom id anywhere on the line: "## Heading {#custom-id}".
            cid = re.search(r'\{#([\w-]+)\}', line)
            if cid:
                anchors.add(cid.group(1).lower())

            # ATX heading ("## Title") or setext heading (prev line underlined
            # by === / ---). Treating a stray underline as a heading only adds a
            # spurious (harmless) anchor, never removes one.
            title = None
            atx = re.match(r'#{1,6}\s+(.*?)\s*#*\s*$', stripped)
            if atx:
                title = atx.group(1)
            elif prev and re.match(r'^(=+|-+)$', stripped):
                title = prev
            if title is not None:
                title = re.sub(r'\{#[\w-]+\}\s*$', '', title).strip()
                slug = self._slugify(title)
                if slug:
                    n = slug_counts.get(slug, 0)
                    anchors.add(slug if n == 0 else f"{slug}-{n}")
                    slug_counts[slug] = n + 1
            prev = stripped

        for m in re.finditer(r'<a\s+[^>]*?(?:name|id)\s*=\s*["\']([^"\']+)["\']', text, re.IGNORECASE):
            anchors.add(m.group(1).lower())

        return target in anchors

    @staticmethod
    def _slugify(title: str) -> str:
        """Approximate GitHub's heading-anchor slug: strip inline links/code,
        lowercase, drop punctuation except hyphens, spaces -> hyphens."""
        t = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', title)  # [text](url) -> text
        t = t.replace('`', '').lower()
        t = re.sub(r'[^\w\s-]', '', t)  # keep letters/digits/_, spaces, hyphens
        return t.strip().replace(' ', '-')

    def _check_with_curl(self, url: str) -> Tuple[bool, str]:
        """Fallback to curl when requests fails."""
        try:
            # Use curl with browser-like headers
            cmd = [
                'curl', '-s', '-I', '--max-time', str(self.timeout),
                '-H', 'User-Agent: Mozilla/5.0 (compatible; RediSearch-LinkChecker/1.0)',
                '-H', 'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                url
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout + 5)

            if result.returncode == 0:
                # Parse the first line to get status code
                lines = result.stdout.strip().split('\n')
                if lines and 'HTTP' in lines[0]:
                    status_line = lines[0]
                    if '200' in status_line or '301' in status_line or '302' in status_line:
                        return True, "OK (curl)"
                    elif '404' in status_line:
                        return False, "HTTP 404 (Not Found)"
                    elif '403' in status_line:
                        return False, "HTTP 403 (Forbidden)"
                    else:
                        return False, f"HTTP error (curl): {status_line}"
                return False, "Invalid response from curl"
            else:
                return False, f"Curl failed: {result.stderr.strip()}"

        except subprocess.TimeoutExpired:
            return False, "Timeout (curl)"
        except Exception as e:
            return False, f"Curl error: {str(e)}"

    def _check_absolute_link(self, url: str) -> Tuple[bool, str]:
        """Check if an absolute URL is valid, including anchor verification."""
        parsed = urlparse(url)
        base_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path,
                             parsed.params, parsed.query, ''))
        anchor = parsed.fragment

        try:
            # First try with requests
            response = self.session.get(base_url, timeout=self.timeout,
                                      allow_redirects=True)
            response.raise_for_status()

            # If there's an anchor, verify it exists in the HTML
            # Github doesn't render generated markdown anchors (e.g. readme) and
            # line-number anchors (e.g., #L207, #L207-L226). Those are rendered
            # client-side via JavaScript and won't appear in static HTML.
            if anchor and parsed.netloc != "github.com":
                content_type = response.headers.get('content-type', '').lower()
                if 'text/html' in content_type:
                    soup = BeautifulSoup(response.content, 'html.parser')

                    # Look for anchor in various ways
                    anchor_found = (
                        soup.find(id=anchor) is not None or
                        soup.find('a', {'name': anchor}) is not None or
                        soup.find(attrs={'name': anchor}) is not None or
                        # GitHub-style header anchors
                        soup.find('h1', id=anchor) is not None or
                        soup.find('h2', id=anchor) is not None or
                        soup.find('h3', id=anchor) is not None or
                        soup.find('h4', id=anchor) is not None or
                        soup.find('h5', id=anchor) is not None or
                        soup.find('h6', id=anchor) is not None
                    )

                    if not anchor_found:
                        return False, f"Anchor '#{anchor}' not found"

            with self._lock:
                self.checked_urls.add(url)
            return True, f"OK ({response.status_code})"

        except (requests.exceptions.HTTPError, requests.exceptions.ConnectionError,
                requests.exceptions.Timeout):
            # If requests fails, try curl as fallback
            if anchor:
                # For URLs with anchors, we can't easily verify anchors with curl
                # so we just check if the base URL is reachable
                curl_result = self._check_with_curl(base_url)
                if curl_result[0]:
                    return True, f"OK (curl, anchor not verified)"
                else:
                    return curl_result
            else:
                return self._check_with_curl(url)

    def check_links_in_file(self, file_path: Path) -> List[Tuple[str, int, bool, str, str]]:
        """Check all links in a single Markdown file."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            print(f"Error reading {file_path}: {e}")
            return []

        links = self.extract_links(content, file_path)
        results = []

        for url, line_num, link_type in links:
            is_valid, message = self.check_url_with_anchor(url, link_type)
            results.append((url, line_num, is_valid, message, link_type))

            # Add delay to be respectful to servers (only for absolute URLs)
            if link_type == 'absolute':
                time.sleep(self.delay)

        return results

    def check_all_files(self, directory: str) -> bool:
        """Check all Markdown files in directory. Returns True if all links are valid."""
        md_files = self.find_markdown_files(directory)

        if not md_files:
            print("No Markdown files found.")
            return True

        print(f"Found {len(md_files)} Markdown files to check...")
        if self.exclude_dirs:
            print(f"Excluding directories: {', '.join(sorted(self.exclude_dirs))}")

        all_valid = True
        total_links = 0
        failed_links = 0

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_file = {
                executor.submit(self.check_links_in_file, file_path): file_path
                for file_path in md_files
            }

            for future in as_completed(future_to_file):
                file_path = future_to_file[future]
                try:
                    results = future.result()

                    if results:
                        file_failures = []
                        file_successes = []

                        for url, line_num, is_valid, message, link_type in results:
                            total_links += 1
                            type_icon = "🌐" if link_type == 'absolute' else "📁"

                            if not is_valid:
                                failed_links += 1
                                all_valid = False
                                file_failures.append((url, line_num, message, type_icon))
                            else:
                                file_successes.append((url, line_num, message, type_icon))

                        # Print file header if there are failures OR if verbose mode
                        if file_failures or (self.verbose and file_successes):
                            print(f"\n📄 {file_path}")

                            # Always show failures
                            for url, line_num, message, type_icon in file_failures:
                                print(f"  ❌ {type_icon} Line {line_num}: {url}")
                                print(f"     └─ {message}")

                            # Show successes only in verbose mode
                            if self.verbose:
                                for url, line_num, message, type_icon in file_successes:
                                    print(f"  ✅ {type_icon} Line {line_num}: {url}")

                except Exception as e:
                    print(f"Error checking {file_path}: {e}")
                    all_valid = False

        successful_links = total_links - failed_links
        print(f"\n📊 Summary:")
        print(f"   Total links checked: {total_links}")
        print(f"   Successful links: {successful_links}")
        print(f"   Failed links: {failed_links}")
        if total_links > 0:
            print(f"   Success rate: {(successful_links / total_links * 100):.1f}%")

        return all_valid


def load_config(config_path: str) -> Dict[str, Any]:
    """Load configuration from JSON file."""
    try:
        with open(config_path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Config file {config_path} not found, using defaults")
        return {}
    except json.JSONDecodeError as e:
        print(f"Error parsing config file {config_path}: {e}")
        return {}


def main():
    parser = argparse.ArgumentParser(description='Check links in Markdown files')
    parser.add_argument('directory', nargs='?', default='.',
                       help='Directory to scan for Markdown files (default: current directory)')
    parser.add_argument('--config', default='scripts/link-check-config.json',
                       help='Configuration file path (default: scripts/link-check-config.json)')
    parser.add_argument('--timeout', type=int,
                       help='Request timeout in seconds (overrides config)')
    parser.add_argument('--max-workers', type=int,
                       help='Maximum number of concurrent workers (overrides config)')
    parser.add_argument('--delay', type=float,
                       help='Delay between requests in seconds (overrides config)')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Show all links (including successful ones)')

    args = parser.parse_args()

    # Load configuration
    config = load_config(args.config)

    # Override config with command line arguments
    if args.timeout is not None:
        config['timeout'] = args.timeout
    if args.max_workers is not None:
        config['max_workers'] = args.max_workers
    if args.delay is not None:
        config['delay'] = args.delay

    checker = LinkChecker(config, verbose=args.verbose)
    success = checker.check_all_files(args.directory)

    if success:
        print("\n🎉 All links are valid!")
        sys.exit(0)
    else:
        print("\n💥 Some links are broken!")
        sys.exit(1)


if __name__ == '__main__':
    main()
