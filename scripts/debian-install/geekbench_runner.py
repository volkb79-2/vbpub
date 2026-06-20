#!/usr/bin/env python3
"""
Geekbench Runner Module
Download, install, run Geekbench and extract results
"""

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class GeekbenchRunner:
    """Download, install and run Geekbench 6 benchmarks"""

    # geekbench.com sits behind Cloudflare and 403s scripted requests, so we do NOT
    # scrape the download page. cdn.geekbench.com is a plain CDN with a stable naming
    # scheme (Geekbench-<major>.<minor>.<patch>-<Linux|LinuxARMPreview>.tar.gz); we
    # probe it directly with a 1-byte ranged GET to discover the latest version.
    CDN_BASE = "https://cdn.geekbench.com"
    MAJOR = 6
    # Safety net used only if probing finds nothing (e.g. CDN naming changes); bump as needed.
    FALLBACK_VERSION = "6.7.1"
    USER_AGENT = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )

    def __init__(self, version=6, work_dir=None):
        if version != 6:
            raise ValueError("Only Geekbench 6 is supported")
        self.version = version
        self.work_dir = work_dir or tempfile.mkdtemp(prefix='geekbench_')
        self.geekbench_path = None
        self.results = {}
        self.download_url = None
        self.artifact_name = None
        self.release_version = None

    def _get_artifact_suffix(self):
        """Return the CDN artifact suffix for this host (Linux | LinuxARMPreview)."""
        system = platform.system()
        if system != 'Linux':
            raise ValueError(f"Unsupported system: {system}")

        machine = platform.machine().lower()
        if machine in ('x86_64', 'amd64'):
            return 'Linux'
        if machine in ('aarch64', 'arm64'):
            return 'LinuxARMPreview'

        raise ValueError(f"Unsupported architecture: {platform.machine()}")

    def _artifact_url(self, version, suffix):
        """Build the CDN tarball URL for a version string + artifact suffix."""
        return f"{self.CDN_BASE}/Geekbench-{version}-{suffix}.tar.gz"

    def _artifact_exists(self, url):
        """True if the CDN serves a real binary artifact at url (1-byte ranged GET).

        A real artifact answers 200/206 with an octet-stream body; a missing version
        answers 404 (text/html). The body is never read, so this stays cheap even
        though the artifacts are ~200 MB. A network error counts as "missing" — the
        consecutive-miss thresholds in the caller keep one blip from ending a scan."""
        request = Request(url, headers={'User-Agent': self.USER_AGENT,
                                        'Range': 'bytes=0-0'})
        try:
            with urlopen(request, timeout=15) as response:
                ctype = response.headers.get('Content-Type', '').lower()
                return response.status in (200, 206) and 'text/html' not in ctype
        except HTTPError:
            return False
        except (URLError, OSError):
            return False

    def _resolve_download_url(self):
        """Resolve the newest Geekbench <MAJOR>.x.y tarball by probing the CDN.

        Two cheap passes: find the highest minor that publishes an x.0, then the
        highest patch within it. Falls back to FALLBACK_VERSION if probing finds
        nothing. Avoids the Cloudflare-protected geekbench.com download page entirely."""
        suffix = self._get_artifact_suffix()

        # Pass 1 — highest minor (every real Geekbench minor ships an x.0).
        best_minor = None
        misses = 0
        minor = 0
        while misses < 2 and minor < 40:
            if self._artifact_exists(self._artifact_url(f"{self.MAJOR}.{minor}.0", suffix)):
                best_minor = minor
                misses = 0
            else:
                misses += 1
            minor += 1

        if best_minor is None:
            print(f"⚠ Could not probe {self.CDN_BASE}; "
                  f"falling back to Geekbench {self.FALLBACK_VERSION}")
            self.release_version = self.FALLBACK_VERSION
        else:
            # Pass 2 — highest patch within best_minor (x.0 already known to exist).
            best_patch = 0
            misses = 0
            patch = 1
            while misses < 2 and patch < 40:
                if self._artifact_exists(
                        self._artifact_url(f"{self.MAJOR}.{best_minor}.{patch}", suffix)):
                    best_patch = patch
                    misses = 0
                else:
                    misses += 1
                patch += 1
            self.release_version = f"{self.MAJOR}.{best_minor}.{best_patch}"

        self.artifact_name = f"Geekbench-{self.release_version}-{suffix}"
        self.download_url = self._artifact_url(self.release_version, suffix)
        return self.download_url

    def _safe_extract(self, tar, path):
        """Extract a tarball while rejecting path traversal entries."""
        root = Path(path).resolve()
        for member in tar.getmembers():
            member_path = (root / member.name).resolve()
            if os.path.commonpath([str(root), str(member_path)]) != str(root):
                raise ValueError(f"Unsafe path in tar archive: {member.name}")
        tar.extractall(path)

    def _find_geekbench_executable(self):
        """Locate the extracted Geekbench binary."""
        preferred_names = ('geekbench6', 'geekbench', 'geekbench_x86_64')

        for name in preferred_names:
            for root, _, files in os.walk(self.work_dir):
                if name in files:
                    candidate = Path(root) / name
                    if candidate.is_file():
                        return str(candidate)

        for root, _, files in os.walk(self.work_dir):
            for file in files:
                if file.startswith('geekbench') and not file.endswith('.tar.gz'):
                    candidate = Path(root) / file
                    if candidate.is_file():
                        return str(candidate)

        return None

    def download_and_extract(self):
        """Download and extract the latest Geekbench artifact."""
        print("Resolving latest Geekbench artifact...")

        try:
            url = self._resolve_download_url()
        except (HTTPError, URLError, ValueError) as e:
            error_msg = f"✗ Failed to determine download URL: {e}"
            print(error_msg)
            self.results['error'] = error_msg
            return False
        except Exception as e:
            error_msg = f"✗ Failed to determine download URL: {e}"
            print(error_msg)
            self.results['error'] = error_msg
            return False

        artifact_label = self.artifact_name or os.path.basename(url).removesuffix('.tar.gz')
        print(f"Downloading {artifact_label}...")
        tarball = os.path.join(self.work_dir, f"{artifact_label}.tar.gz")

        try:
            request = Request(url, headers={'User-Agent': self.USER_AGENT})
            with urlopen(request, timeout=300) as response, open(tarball, 'wb') as out:
                shutil.copyfileobj(response, out)
            print(f"✓ Downloaded to {tarball}")
        except (HTTPError, URLError) as e:
            error_msg = f"✗ Download failed: {e}"
            print(error_msg)
            self.results['error'] = error_msg
            print(f"Attempted URL: {url}")
            return False
        except Exception as e:
            error_msg = f"✗ Download failed with unexpected error: {e}"
            print(error_msg)
            self.results['error'] = error_msg
            return False
        
        # Verify downloaded file
        if not os.path.exists(tarball):
            error_msg = f"✗ Downloaded file not found: {tarball}"
            print(error_msg)
            self.results['error'] = error_msg
            return False
        
        file_size = os.path.getsize(tarball)
        if file_size < 1024:  # Less than 1KB is suspicious
            error_msg = f"✗ Downloaded file too small ({file_size} bytes) - likely an error page"
            print(error_msg)
            self.results['error'] = error_msg
            # Try to read and show content if it's text (use binary mode to avoid decode errors)
            try:
                with open(tarball, 'rb') as f:
                    content = f.read(500)
                    # Try to decode as UTF-8, but don't fail if it's binary
                    try:
                        content_str = content.decode('utf-8', errors='replace')
                        print(f"File content preview: {content_str}")
                    except:
                        print(f"File appears to be binary or corrupt (first bytes): {content[:50]}")
            except:
                pass
            return False
        
        print(f"Downloaded file size: {file_size / 1024 / 1024:.1f} MB")
        
        # Extract
        try:
            print("Extracting...")
            with tarfile.open(tarball, 'r:gz') as tar:
                self._safe_extract(tar, self.work_dir)

            self.geekbench_path = self._find_geekbench_executable()
            if self.geekbench_path:
                os.chmod(self.geekbench_path, 0o755)
                print(f"✓ Extracted to {self.geekbench_path}")
                return True
            
            error_msg = "✗ Geekbench executable not found after extraction"
            print(error_msg)
            # List what was extracted for debugging
            print("Extracted files:")
            for root, dirs, files in os.walk(self.work_dir):
                for file in files:
                    print(f"  {os.path.join(root, file)}")
            self.results['error'] = error_msg
            return False
            
        except tarfile.ReadError as e:
            error_msg = f"✗ Extraction failed: Invalid tar.gz file - {e}"
            print(error_msg)
            self.results['error'] = error_msg
            return False
        except Exception as e:
            error_msg = f"✗ Extraction failed: {e}"
            print(error_msg)
            self.results['error'] = error_msg
            return False
    
    def run_benchmark(self):
        """Run Geekbench benchmark"""
        if not self.geekbench_path:
            error_msg = "✗ Geekbench not installed. Run download_and_extract() first."
            print(error_msg)
            self.results['error'] = error_msg
            return False
        
        version_label = self.release_version or self.version
        print(f"\nRunning Geekbench {version_label} benchmark...")
        print("This may take 5-10 minutes...\n")
        
        try:
            # Check if Pro version by checking help output
            help_result = subprocess.run(
                [self.geekbench_path, '--help'],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            # Check for both --export-json and --no-upload availability
            has_export_json = '--export-json' in help_result.stdout
            has_no_upload = '--no-upload' in help_result.stdout
            is_pro = has_export_json and has_no_upload
            
            if is_pro:
                print("Detected Geekbench Pro - using JSON export without upload")
                # Run benchmark with JSON output and no upload
                result = subprocess.run(
                    [self.geekbench_path, '--export-json', '--no-upload'],
                    cwd=os.path.dirname(self.geekbench_path),
                    capture_output=True,
                    text=True,
                    timeout=900  # 15 minutes max
                )
            else:
                print("Detected Geekbench Free - running without restricted flags")
                print("⚠ Results will be uploaded to Geekbench Browser (free version limitation)")
                # Run benchmark without any restricted flags (free version)
                # Free version will upload results automatically
                result = subprocess.run(
                    [self.geekbench_path],
                    cwd=os.path.dirname(self.geekbench_path),
                    capture_output=True,
                    text=True,
                    timeout=900  # 15 minutes max
                )
            
            # Print stdout for debugging
            if result.stdout:
                print(result.stdout)
            
            # Check for errors in stderr
            if result.stderr:
                print(f"Stderr output: {result.stderr}", file=sys.stderr)
            
            # Check return code
            if result.returncode != 0:
                error_msg = f"✗ Benchmark exited with code {result.returncode}"
                print(error_msg)
                self.results['error'] = error_msg
                self.results['stderr'] = result.stderr
                self.results['stdout'] = result.stdout
                return False
            
            if is_pro:
                # Find JSON result file
                result_file = None
                for root, dirs, files in os.walk(os.path.dirname(self.geekbench_path)):
                    for file in files:
                        if file.endswith('.gb' + str(self.version)):
                            result_file = os.path.join(root, file)
                            break
                    if result_file:
                        break
                
                if result_file and os.path.exists(result_file):
                    with open(result_file, 'r') as f:
                        self.results = json.load(f)
                    print(f"✓ Results saved to {result_file}")
                    return True
                else:
                    error_msg = "✗ Result file not found after benchmark completed"
                    print(error_msg)
                    # Search for any .gb files for debugging
                    all_gb_files = []
                    for root, dirs, files in os.walk(os.path.dirname(self.geekbench_path)):
                        for file in files:
                            if '.gb' in file:
                                all_gb_files.append(os.path.join(root, file))
                    
                    if all_gb_files:
                        print(f"Found these .gb files: {all_gb_files}")
                        error_msg += f". Found: {all_gb_files}"
                    else:
                        print("No .gb files found in working directory")
                    
                    self.results['error'] = error_msg
                    self.results['stdout'] = result.stdout
                    return False
            else:
                # Free version - parse stdout for results
                print("✓ Benchmark completed (Free version)")
                print("⚠ JSON results not available in Geekbench Free")
                
                # Try to extract basic scores from stdout
                import re
                single_score = None
                multi_score = None
                
                single_match = re.search(r'Single-Core Score\s+(\d+)', result.stdout)
                if single_match:
                    single_score = int(single_match.group(1))
                
                multi_match = re.search(r'Multi-Core Score\s+(\d+)', result.stdout)
                if multi_match:
                    multi_score = int(multi_match.group(1))
                
                # Extract result URL (free version uploads automatically)
                result_url = None
                claim_url = None
                
                # Pattern: https://browser.geekbench.com/v6/cpu/15956833
                url_pattern = r'https://browser\.geekbench\.com/v\d+/cpu/\d+'
                url_match = re.search(url_pattern, result.stdout)
                if url_match:
                    result_url = url_match.group(0)
                    print(f"✓ Found result URL: {result_url}")
                
                # Pattern: https://browser.geekbench.com/v6/cpu/15956833/claim?key=526696
                claim_pattern = r'https://browser\.geekbench\.com/v\d+/cpu/\d+/claim\?key=\w+'
                claim_match = re.search(claim_pattern, result.stdout)
                if claim_match:
                    claim_url = claim_match.group(0)
                    print(f"✓ Found claim URL: {claim_url}")
                
                # Build results
                self.results = {
                    'free_version': True,
                    'stdout': result.stdout
                }
                
                if single_score or multi_score:
                    self.results['score'] = {
                        'singlecore_score': single_score,
                        'multicore_score': multi_score
                    }
                    print(f"✓ Extracted scores - Single: {single_score}, Multi: {multi_score}")
                else:
                    print("⚠ Could not extract scores from output")
                    # Try to scrape from URL if available
                    if result_url:
                        print("Attempting to scrape scores from result URL...")
                        scraped_scores = self._scrape_scores_from_url(result_url)
                        if scraped_scores:
                            self.results['score'] = scraped_scores
                            print(f"✓ Scraped scores - Single: {scraped_scores.get('singlecore_score')}, Multi: {scraped_scores.get('multicore_score')}")
                        else:
                            print("⚠ Could not scrape scores from URL")
                
                if result_url:
                    self.results['result_url'] = result_url
                if claim_url:
                    self.results['claim_url'] = claim_url
                
                return True
                
        except subprocess.TimeoutExpired:
            error_msg = "✗ Benchmark timed out after 15 minutes"
            print(error_msg)
            print("This may indicate a system performance issue or Geekbench hang.")
            print("Consider checking system logs and available resources.")
            self.results['error'] = error_msg
            return False
        except Exception as e:
            error_msg = f"✗ Benchmark failed: {e}"
            print(error_msg)
            self.results['error'] = str(e)
            return False
    
    def _scrape_scores_from_url(self, url):
        """
        Scrape Geekbench scores from the result URL.
        This is a fallback when scores aren't available in the CLI output.
        
        Args:
            url: The Geekbench result URL (e.g., https://browser.geekbench.com/v6/cpu/12345)
        
        Returns:
            Dictionary with singlecore_score and multicore_score, or None if scraping fails
        """
        try:
            # Use curl to fetch the HTML page
            result = subprocess.run(
                ['curl', '-sL', '--max-time', '10', url],
                capture_output=True,
                text=True,
                timeout=15
            )
            
            if result.returncode != 0:
                return None
            
            html = result.stdout
            
            # Parse scores from HTML
            # Geekbench result pages have scores in specific sections
            # Example patterns from actual pages:
            # <div class="score" ...>1234</div>
            # <span class="score">1234</span>
            # In tables with "Single-Core Score" and "Multi-Core Score" headers
            
            single_score = None
            multi_score = None
            
            # Pattern 1: Look for score values in table rows after "Single-Core Score" text
            # This is the most reliable pattern for Geekbench browser pages
            single_patterns = [
                r'Single-Core\s+Score[^>]*>[\s]*<[^>]*>[\s]*(\d+)',  # Score in next element
                r'Single-Core\s+Score.*?<(?:div|span|td)[^>]*>[\s]*(\d+)',  # Score in div/span/td
                r'Single-Core\s+Score.*?(\d{3,5})',  # Any 3-5 digit number after label
            ]
            
            for pattern in single_patterns:
                match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
                if match:
                    single_score = int(match.group(1))
                    print(f"Extracted single-core score: {single_score} (pattern: {pattern[:50]}...)")
                    break
            
            # Pattern 2: Look for score values after "Multi-Core Score" text
            multi_patterns = [
                r'Multi-Core\s+Score[^>]*>[\s]*<[^>]*>[\s]*(\d+)',
                r'Multi-Core\s+Score.*?<(?:div|span|td)[^>]*>[\s]*(\d+)',
                r'Multi-Core\s+Score.*?(\d{3,5})',
            ]
            
            for pattern in multi_patterns:
                match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
                if match:
                    multi_score = int(match.group(1))
                    print(f"Extracted multi-core score: {multi_score} (pattern: {pattern[:50]}...)")
                    break
            
            # Pattern 3: Alternative - look for scores in result summary section
            # Geekbench pages often have a summary section with class="score" elements
            if not single_score or not multi_score:
                # Find all elements with class containing "score"
                score_divs = re.findall(r'<(?:div|span|td)[^>]*class="[^"]*score[^"]*"[^>]*>[\s]*(\d{3,5})', html, re.IGNORECASE)
                if len(score_divs) >= 2:
                    # Usually first score is single-core, second is multi-core
                    if not single_score and score_divs[0]:
                        single_score = int(score_divs[0])
                        print(f"Extracted single-core score from score divs: {single_score}")
                    if not multi_score and len(score_divs) > 1 and score_divs[1]:
                        multi_score = int(score_divs[1])
                        print(f"Extracted multi-core score from score divs: {multi_score}")
            
            # Pattern 4: Look in meta tags (sometimes scores are in OpenGraph tags)
            if not single_score or not multi_score:
                # Try meta tags
                meta_match = re.search(r'<meta[^>]*property="og:description"[^>]*content="[^"]*(\d{3,5})[^"]*(\d{3,5})', html, re.IGNORECASE)
                if meta_match:
                    if not single_score:
                        single_score = int(meta_match.group(1))
                        print(f"Extracted single-core score from meta tags: {single_score}")
                    if not multi_score:
                        multi_score = int(meta_match.group(2))
                        print(f"Extracted multi-core score from meta tags: {multi_score}")
            
            if single_score or multi_score:
                return {
                    'singlecore_score': single_score,
                    'multicore_score': multi_score
                }
            else:
                print(f"⚠ Could not extract scores from {url}")
                # Debug: save first 2000 chars of HTML to see structure
                print(f"HTML snippet (first 500 chars): {html[:500]}")
            
            return None
            
        except Exception as e:
            print(f"⚠ Error scraping scores from URL: {e}")
            return None
    
    def upload_results(self):
        """Upload results to Geekbench Browser and get claim URL"""
        if not self.geekbench_path:
            print("✗ Geekbench not installed")
            return None
        
        if not self.results:
            print("✗ No results to upload. Run benchmark first.")
            return None
        
        print("\nUploading results to Geekbench Browser...")
        
        try:
            # Run geekbench with upload
            result = subprocess.run(
                [self.geekbench_path],
                cwd=os.path.dirname(self.geekbench_path),
                capture_output=True,
                text=True,
                timeout=900
            )
            
            # Extract URL from output
            url_pattern = r'https://browser\.geekbench\.com/v\d+/cpu/\d+'
            match = re.search(url_pattern, result.stdout)
            
            if match:
                url = match.group(0)
                print(f"✓ Results uploaded: {url}")
                
                # Extract claim URL if present
                claim_pattern = r'claim.*?(https://[^\s]+)'
                claim_match = re.search(claim_pattern, result.stdout, re.IGNORECASE)
                if claim_match:
                    claim_url = claim_match.group(1)
                    print(f"✓ Claim URL: {claim_url}")
                    return {'result_url': url, 'claim_url': claim_url}
                
                return {'result_url': url}
            else:
                print("✗ Could not extract URL from output")
                return None
                
        except Exception as e:
            print(f"✗ Upload failed: {e}")
            return None
    
    def get_summary(self):
        """Get formatted summary of results"""
        if not self.results:
            return "No results available"
        
        version_label = self.release_version or self.version
        summary = f"<b>🏆 Geekbench {version_label} Results</b>\n\n"
        
        # System info
        if 'system' in self.results:
            system = self.results['system']
            summary += f"<b>System:</b> {system.get('model', 'Unknown')}\n"
            summary += f"<b>OS:</b> {system.get('operating_system', 'Unknown')}\n"
            summary += f"<b>Processor:</b> {system.get('processor', 'Unknown')}\n"
            summary += f"<b>Memory:</b> {system.get('memory', 'Unknown')}\n\n"
        
        # Scores
        if 'score' in self.results:
            score = self.results['score']
            summary += f"<b>📊 Scores:</b>\n"
            summary += f"  Single-Core: {score.get('singlecore_score', 'N/A')}\n"
            summary += f"  Multi-Core: {score.get('multicore_score', 'N/A')}\n"
        
        # URLs (for free version)
        if 'result_url' in self.results:
            summary += f"\n<b>🔗 Result URL:</b>\n  {self.results['result_url']}\n"
        
        if 'claim_url' in self.results:
            summary += f"\n<b>📌 Claim URL:</b>\n  {self.results['claim_url']}\n"
        
        return summary
    
    def cleanup(self):
        """Clean up temporary files"""
        try:
            import shutil
            if os.path.exists(self.work_dir):
                shutil.rmtree(self.work_dir)
                print(f"✓ Cleaned up {self.work_dir}")
        except Exception as e:
            print(f"⚠ Cleanup failed: {e}")


def main():
    """CLI interface"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Geekbench Runner',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --run            # Download, install and run benchmark
  %(prog)s --run --upload   # Run and upload results
        """
    )
    
    parser.add_argument('--version', type=int, default=6, choices=[6],
                       help='Geekbench version (Geekbench 6 only)')
    parser.add_argument('--run', action='store_true',
                       help='Run benchmark')
    parser.add_argument('--upload', action='store_true',
                       help='Upload results to Geekbench Browser')
    parser.add_argument('--work-dir', metavar='DIR',
                       help='Working directory (default: temp dir)')
    parser.add_argument('--no-cleanup', action='store_true',
                       help='Do not cleanup temp files')
    
    args = parser.parse_args()
    
    runner = GeekbenchRunner(version=args.version, work_dir=args.work_dir)
    
    try:
        if args.run:
            # Download and extract
            if not runner.download_and_extract():
                return 1
            
            # Run benchmark
            if not runner.run_benchmark():
                return 1
            
            # Show summary
            print("\n" + "="*60)
            print(runner.get_summary())
            print("="*60)
            
            # Upload if requested
            if args.upload:
                urls = runner.upload_results()
                if urls:
                    print(f"\nResult URL: {urls.get('result_url')}")
                    if 'claim_url' in urls:
                        print(f"Claim URL: {urls['claim_url']}")
        else:
            parser.print_help()
        
        return 0
        
    finally:
        if not args.no_cleanup:
            runner.cleanup()


if __name__ == '__main__':
    sys.exit(main())
