<?php
declare(strict_types=1);

/**
 * .htaccess capability probe for shared webhosting - CLI and browser in one file.
 *
 * Writes disposable .htaccess files into a random probe folder below the web root, requests them
 * over HTTP and reports which directives the host honours (rewrite, headers, access control,
 * Basic Auth, REMOTE_USER, .user.ini, ...). By default it ALSO tests the web root .htaccess and the
 * file one level above it, one short-lived block at a time (backed up first, removed right after).
 * Needs PHP 8.1+ (the test machine, not the hosting PHP version, is what matters for CLI use).
 *
 * CLI (run from the DOMAIN folder, the one that contains httpdocs/, not from inside httpdocs):
 *   php test-webspace-htaccess-support.php mydomain.tld
 *   php test-webspace-htaccess-support.php mydomain.tld --verbose --keep --docroot=public_html
 *   php test-webspace-htaccess-support.php mydomain.tld --no-domain --json=report.json
 *
 * Browser (hosts without shell access or without a folder above the web root):
 *   1. set HTA_WEB_TOKEN below to a long random string (openssl rand -hex 16)
 *   2. upload this file into the web root (or any folder below it)
 *   3. open https://mydomain.tld/test-webspace-htaccess-support.php?token=THE-TOKEN
 *      optional: &verbose=1  &format=json  &root=0 (no root tests)  &domain=0  &docroot=0  &keep=1
 *                &timeout=N (2..120)  &insecure=1
 *      If the site is behind Basic Auth, open the page logged in: the credentials are forwarded.
 *   4. DELETE THE FILE AGAIN. In this mode the folder above the web root is only tested when
 *      PHP may write there (open_basedir); otherwise that placement is reported as skipped.
 * Root tests briefly append one block to the live .htaccess file(s); a broken block could cause
 * 500 errors for a few seconds, so do not run it during important traffic.
 */

// Browser mode only: shared secret for ?token= (leave empty to disable browser mode).
const HTA_WEB_TOKEN = '';

const SCRIPT_VERSION = '1.4.0';

$GLOBALS['HTA_VERBOSE'] = false;   // long per-test blocks and the long analysis
$GLOBALS['HTA_QUIET'] = false;     // JSON on stdout: no text at all
$GLOBALS['HTA_COLOR'] = false;

function out(string $s = ''): void
{
    if ($GLOBALS['HTA_QUIET']) return;
    echo $s, PHP_EOL;
    if (PHP_SAPI !== 'cli') @flush();
}
/** Output that only appears with --verbose / &verbose=1. */
function vout(string $s = ''): void { if ($GLOBALS['HTA_VERBOSE']) out($s); }
function err(string $s): void
{
    if (PHP_SAPI === 'cli') fwrite(STDERR, $s . PHP_EOL); else out('! ' . $s);
}

function paint(string $label): string
{
    if (!$GLOBALS['HTA_COLOR']) return $label;
    $code = ['OK' => '32', 'FAIL' => '31', 'WARN' => '33', '??' => '35'][trim($label)] ?? '0';
    return "\033[{$code}m{$label}\033[0m";
}

/** OK = works (SUPPORTED/ACTIVE/OK), FAIL = rejected, WARN = accepted without effect, ?? = undecided. */
function shortState(array $result): string
{
    return match (resultState($result)) {
        'REJECTED' => 'FAIL',
        'ACCEPTED/NO-EFFECT' => 'WARN',
        'INCONCLUSIVE' => ' ?? ',
        default => ' OK ',
    };
}

function reportLine(array $result): void
{
    if ($GLOBALS['HTA_VERBOSE']) return;
    $label = shortState($result);
    $line = '[' . paint($label) . '] ' . $result['name'];
    if (trim($label) !== 'OK') $line .= ' - ' . $result['why'];
    out($line);
}

function flagParam(string $name, bool $default): bool
{
    if (!isset($_GET[$name])) return $default;
    return !in_array(strtolower((string)$_GET[$name]), ['0', 'no', 'false', 'off'], true);
}

/** open_basedir-aware reachability test for a directory (silent). */
function pathReachable(string $path): bool
{
    $ob = (string)ini_get('open_basedir');
    if ($ob === '') return true;
    $real = @realpath($path);
    if ($real === false) $real = rtrim($path, '/');
    foreach (explode(PATH_SEPARATOR, $ob) as $allowed) {
        $a = rtrim(trim($allowed), '/');
        if ($a === '') continue;
        $ar = @realpath($a);
        if ($ar !== false) $a = rtrim($ar, '/');
        if ($real === $a || str_starts_with($real, $a . '/')) return true;
    }
    return false;
}

function usage(int $exit = 0): never
{
    $name = basename(__FILE__);
    $text = <<<TXT
Usage: php {$name} <domain-or-base-url> [options]
Place this file in the folder that CONTAINS the web root (httpdocs/), not inside it;
the web root is located relative to this file, not to the current directory.

Examples:
  php {$name} mydomain.tld
  php {$name} https://mydomain.tld --verbose --keep --docroot=public_html
  php {$name} mydomain.tld --no-domain --json=report.json

Options:
  --docroot=DIR     web root folder, relative to this file or absolute (default: httpdocs).
  --no-root         do NOT touch .htaccess files outside the probe folder (= both below).
  --no-docroot      skip the test of <web root>/.htaccess.
  --no-domain       skip the test of the .htaccess above the web root.
  --auth=USER:PASS  send Basic Auth credentials (site already behind a login;
                    visible in the process list).
  --verbose, -v     long output per test and the long diagnostic analysis.
  --json=FILE       also write a machine-readable report ('-' = JSON only, on stdout).
  --no-color        no ANSI colours (default: colours only on a terminal; NO_COLOR is honoured).
  --color           force colours.
  --keep            keep the probe folder after the run.
  --timeout=N       HTTP timeout per probe in seconds (default 15; 2..120).
  --root-timeout=N  HTTP timeout while a root .htaccess is modified (default 5; 2..15).
  --insecure        ignore TLS certificate errors.
  --help, -h        this text.

By default the web root .htaccess and the one above it are each modified for one request at a
time (appended block, backed up first, removed right after). Browser mode: see the comment at
the top of this file (HTA_WEB_TOKEN).

Result labels: OK = works, FAIL = rejected by the server, WARN = accepted but without effect,
?? = could not be decided (use --verbose).
Exit codes: 0 = probe finished (see results), 1 = fatal error, 2 = usage error,
3 = a root .htaccess could not be restored automatically (backup path is printed).
TXT;
    if ($exit === 0) echo $text, PHP_EOL; else fwrite(STDERR, $text . PHP_EOL);
    exit($exit);
}

/** CLI usage error: one clean line on stderr, exit 2. */
function usageError(string $msg): never
{
    fwrite(STDERR, basename(__FILE__) . ': ' . $msg . PHP_EOL . 'Try --help.' . PHP_EOL);
    exit(2);
}

function disabledFunction(string $name): bool
{
    $disabled = array_filter(array_map('trim', explode(',', (string)ini_get('disable_functions'))));
    return in_array($name, $disabled, true);
}

function commandExists(string $command): ?string
{
    foreach (["/usr/bin/{$command}", "/bin/{$command}", "/usr/local/bin/{$command}"] as $candidate) {
        if (is_file($candidate) && is_executable($candidate)) return $candidate;
    }
    if (function_exists('exec') && !disabledFunction('exec')) {
        $result = [];
        $code = 1;
        @exec('command -v ' . escapeshellarg($command) . ' 2>/dev/null', $result, $code);
        if ($code === 0 && $result) return trim($result[0]);
    }
    if (function_exists('proc_open') && !disabledFunction('proc_open')) {
        $proc = @proc_open('command -v ' . escapeshellarg($command), [1 => ['pipe','w'], 2 => ['pipe','w']], $pipes);
        if (is_resource($proc)) {
            $stdout = trim(stream_get_contents($pipes[1]) ?: '');
            stream_get_contents($pipes[2]);
            fclose($pipes[1]); fclose($pipes[2]);
            $code = proc_close($proc);
            if ($code === 0 && $stdout !== '') return strtok($stdout, "\n") ?: null;
        }
    }
    return null;
}

function normalizeBaseUrl(string $input): string
{
    $input = trim($input);
    if ($input === '') {
        throw new RuntimeException('Empty FQDN/base URL.');
    }
    if (preg_match('~^[a-z][a-z0-9+.-]*://~i', $input) && !preg_match('~^https?://~i', $input)) {
        throw new RuntimeException('Only http:// and https:// URLs are supported.');
    }
    if (!preg_match('~^https?://~i', $input)) {
        $input = 'https://' . $input;
    }
    $parts = parse_url($input);
    if ($parts === false || empty($parts['host'])) {
        throw new RuntimeException("Could not parse base URL: {$input}");
    }
    $scheme = strtolower((string)($parts['scheme'] ?? 'https'));
    if (!in_array($scheme, ['http', 'https'], true)) {
        throw new RuntimeException('Only http:// and https:// URLs are supported.');
    }
    $host = (string)$parts['host'];
    $port = isset($parts['port']) ? ':' . (int)$parts['port'] : '';
    $path = isset($parts['path']) ? rtrim((string)$parts['path'], '/') : '';
    if ($path === '/') {
        $path = '';
    }
    if (!empty($parts['query']) || !empty($parts['fragment'])) {
        throw new RuntimeException('Do not include query strings or fragments in the base URL.');
    }
    return $scheme . '://' . $host . $port . $path;
}

function mkdirp(string $dir): void
{
    if (is_dir($dir)) {
        return;
    }
    if (!@mkdir($dir, 0755, true) && !is_dir($dir)) {
        throw new RuntimeException("Cannot create directory: {$dir}");
    }
}

function writeFileStrict(string $path, string $content, int $mode = 0644): void
{
    mkdirp(dirname($path));
    $n = @file_put_contents($path, $content, LOCK_EX);
    if ($n === false || $n !== strlen($content)) {
        throw new RuntimeException("Cannot write file: {$path}");
    }
    @chmod($path, $mode);
    clearstatcache(true, $path);
}

function rrmdir(string $dir): void
{
    if (!is_dir($dir) && !is_link($dir)) {
        return;
    }
    if (is_link($dir) || is_file($dir)) {
        @unlink($dir);
        return;
    }
    $items = @scandir($dir);
    if ($items === false) {
        return;
    }
    foreach ($items as $item) {
        if ($item === '.' || $item === '..') continue;
        $p = $dir . DIRECTORY_SEPARATOR . $item;
        if (is_dir($p) && !is_link($p)) rrmdir($p);
        else @unlink($p);
    }
    @rmdir($dir);
}

function parseHeaderBlocks(string $raw): array
{
    // curl -D may contain multiple header blocks (e.g. 100 Continue, redirects).
    $raw = str_replace("\r\n", "\n", $raw);
    $blocks = preg_split("~\n\n+~", trim($raw)) ?: [];
    $parsed = [];
    foreach ($blocks as $block) {
        $lines = explode("\n", $block);
        if (!$lines || !preg_match('~^HTTP/\S+\s+\d{3}~i', trim($lines[0]))) {
            continue;
        }
        $statusLine = trim(array_shift($lines));
        $headers = [];
        foreach ($lines as $line) {
            if (!str_contains($line, ':')) continue;
            [$k, $v] = explode(':', $line, 2);
            $k = strtolower(trim($k));
            $v = trim($v);
            $headers[$k][] = $v;
        }
        $parsed[] = ['status_line' => $statusLine, 'headers' => $headers];
    }
    return $parsed;
}

/**
 * Add $defaults unless the request already names the same header; then drop headers with an
 * empty value (so 'Authorization:' in a request means "send no Authorization at all").
 */
function mergeHeaders(array $headers, array $defaults): array
{
    $names = [];
    foreach ($headers as $h) $names[strtolower(trim(explode(':', $h, 2)[0]))] = true;
    foreach ($defaults as $h) {
        if (!isset($names[strtolower(trim(explode(':', $h, 2)[0]))])) $headers[] = $h;
    }
    return array_values(array_filter($headers, static fn($h) => trim(explode(':', $h, 2)[1] ?? '') !== ''));
}

function headerValue(array $response, string $name): ?string
{
    $blocks = $response['header_blocks'] ?? [];
    if (!$blocks) return null;
    $last = $blocks[count($blocks) - 1];
    $key = strtolower($name);
    if (empty($last['headers'][$key])) return null;
    return implode(', ', $last['headers'][$key]);
}

function printableBody(string $body, int $maxBytes): string
{
    $body = substr($body, 0, $maxBytes);
    // Keep tabs/newlines; replace other control/binary bytes.
    return preg_replace_callback('/[^\x09\x0A\x0D\x20-\x7E]/', static fn($m) => sprintf('\\x%02X', ord($m[0])), $body) ?? '';
}

final class LogWatcher
{
    /** @var array<string,int> */
    private array $offsets = [];
    /** @var string[] */
    public array $files = [];

    public function __construct(string $domainDir)
    {
        $candidates = [
            $domainDir . '/logs/error_log',
            $domainDir . '/logs/proxy_error_log',
            $domainDir . '/logs/php_error.log',
            $domainDir . '/logs/error.log',
            $domainDir . '/log/error_log',
        ];
        foreach ($candidates as $f) {
            if (is_file($f) && is_readable($f)) {
                $real = realpath($f) ?: $f;
                if (!in_array($real, $this->files, true)) {
                    $this->files[] = $real;
                }
            }
        }
        $this->mark();
    }

    public function mark(): void
    {
        clearstatcache();
        foreach ($this->files as $f) {
            $size = @filesize($f);
            $this->offsets[$f] = is_int($size) ? $size : 0;
        }
    }

    /** @return array<string,string> */
    public function delta(int $maxBytesPerFile = 8192): array
    {
        $out = [];
        clearstatcache();
        foreach ($this->files as $f) {
            $old = $this->offsets[$f] ?? 0;
            $size = @filesize($f);
            if (!is_int($size) || $size <= $old) continue;
            $len = min($size - $old, $maxBytesPerFile);
            $fh = @fopen($f, 'rb');
            if (!$fh) continue;
            @fseek($fh, $old);
            $data = (string)@fread($fh, $len);
            @fclose($fh);
            if ($data !== '') $out[$f] = $data;
        }
        $this->mark();
        return $out;
    }
}


/**
 * Convert low-level probe classifications into user-facing states.
 * This deliberately separates a known rejection from a genuinely ambiguous test.
 */
function resultState(array $result): string
{
    $c = strtolower((string)($result['classification'] ?? ''));
    $ok = (bool)($result['ok'] ?? false);

    if ($c === 'baseline-ok' || $c === 'web-runtime-detected' || $c === 'files-container-public-ok') return 'OK';
    if ($c === 'htaccess-parsed' || $c === 'scope-parent-supported' || $c === 'nested-htaccess-supported' || $c === 'root-placement-active') return 'ACTIVE';
    // Accepted (HTTP 200) but without the requested effect: Basic Auth not enforced, user not passed to PHP, .user.ini ignored.
    if (in_array($c, ['php_value-accepted-not-effective', 'basicauth-not-effective', 'remote-user-not-effective', 'user_ini-not-effective'], true)) return 'ACCEPTED/NO-EFFECT';
    if (str_contains($c, 'rejected') || str_contains($c, 'not-supported') || str_ends_with($c, '-exposed')) return 'REJECTED';
    if (str_contains($c, 'inconclusive') || str_contains($c, 'not-effective') || str_contains($c, 'possibly-ignored') || str_contains($c, 'unexpected') || str_ends_with($c, '-failed')) return 'INCONCLUSIVE';
    if ($ok) return 'SUPPORTED';
    return 'INCONCLUSIVE';
}

function resultFamily(array $result): string
{
    $name = strtolower((string)($result['name'] ?? ''));
    $c = strtolower((string)($result['classification'] ?? ''));
    if (str_contains($name, 'baseline') || str_contains($name, 'web php runtime')) return 'Environment / control';
    if (str_contains($name, 'basic auth') || str_contains($name, 'dotfile') || str_contains($name, '.htaccess file')) return 'AuthConfig / access control';
    if (str_contains($name, '.user.ini')) return 'PHP handler integration';
    if (str_contains($c, 'placement') || str_contains($c, 'scope-parent') || str_contains($c, 'nested-htaccess') || $c === 'htaccess-parsed') return 'Placement / parsing';
    if (str_contains($name, 'php_value') || str_contains($c, 'php_value')) return 'PHP handler integration';
    if (str_contains($name, 'require') || str_contains($name, '<files>') || str_contains($c, 'require') || str_contains($c, 'files-container')) return 'AuthConfig / access control';
    if (str_contains($name, 'directoryindex')) return 'Indexes';
    if (str_contains($name, 'options ') || str_contains($c, 'options-') || str_contains($c, 'symlink')) return 'Options';
    if (str_contains($name, 'rewrite') || str_contains($name, 'header') || str_contains($name, 'redirect') || str_contains($name, 'errordocument') || str_contains($name, 'addtype') || str_contains($name, 'expires') || str_contains($name, 'deflate') || str_contains($name, 'fallbackresource')) return 'FileInfo / modules';
    return 'Other';
}

function compactSetting(string $directives, int $max = 150): string
{
    $directives = preg_replace('/\\s+/', ' ', trim($directives)) ?? trim($directives);
    if ($directives === '') return '(none; request/control test)';
    if (strlen($directives) <= $max) return $directives;
    return substr($directives, 0, $max - 3) . '...';
}

function findResultByClassification(array $results, string $classification, ?string $placement = null): ?array
{
    foreach ($results as $r) {
        if (($r['classification'] ?? '') !== $classification) continue;
        if ($placement !== null && ($r['placement'] ?? '') !== $placement) continue;
        return $r;
    }
    return null;
}

function anyClassification(array $results, array $classes): bool
{
    foreach ($results as $r) if (in_array((string)($r['classification'] ?? ''), $classes, true)) return true;
    return false;
}

function allClassificationsPresent(array $results, array $classes): bool
{
    foreach ($classes as $class) if (findResultByClassification($results, $class) === null) return false;
    return true;
}

function extractRuntimeValue(array $results, string $key): ?string
{
    $r = findResultByClassification($results, 'web-runtime-detected');
    if ($r === null) return null;
    $body = (string)($r['response']['body'] ?? '');
    if (preg_match('/^' . preg_quote($key, '/') . '=(.*)$/m', $body, $m)) return trim($m[1]);
    return null;
}

function printAtAGlance(array $results, bool $testDocroot, bool $testDomain): void
{
    vout('AT A GLANCE');
    vout(str_repeat('-', 78));
    $nested=findResultByClassification($results,'htaccess-parsed');
    vout('  Nested disposable .htaccess : ' . ($nested !== null && resultState($nested)==='ACTIVE' ? 'ACTIVE' : 'NOT PROVEN'));
    foreach ([['DocumentRoot',$testDocroot],['DomainParent',$testDomain]] as [$placement,$enabled]) {
        if (!$enabled) { vout(sprintf('  %-28s: NOT TESTED', $placement . ' .htaccess')); continue; }
        $active=false;
        foreach($results as $r) if(($r['placement']??'')===$placement && ($r['classification']??'')==='root-placement-active') {$active=true;break;}
        vout(sprintf('  %-28s: %s', $placement . ' .htaccess', $active ? 'ACTIVE' : 'NOT PROVEN'));
    }

    $byState=['REJECTED'=>[],'ACCEPTED/NO-EFFECT'=>[],'INCONCLUSIVE'=>[]];
    foreach($results as $r) {
        $st=resultState($r);
        if(isset($byState[$st])) $byState[$st][]=$r;
    }
    foreach($byState as $state=>$items) {
        vout(); vout('  ' . $state . ':');
        if(!$items) { vout('    (none)'); continue; }
        foreach($items as $r) {
            $where=(string)($r['htaccess_path'] ?? $r['placement'] ?? '');
            vout('    - ' . $r['name'] . ($where !== '' ? ' @ ' . $where : ''));
            vout('      ' . $r['why']);
        }
    }
    vout();
}

function printPlacementAnalysis(array $results, string $domainDir, string $docroot, string $probeRoot, bool $testDocroot, bool $testDomain): void
{
    vout('PLACEMENT ANALYSIS');
    vout(str_repeat('-', 78));

    $parsed = findResultByClassification($results, 'htaccess-parsed');
    $scope = findResultByClassification($results, 'scope-parent-supported');
    $nested = findResultByClassification($results, 'nested-htaccess-supported');
    vout('1) Disposable nested .htaccess files');
    vout('   Area       : ' . $probeRoot . '/<test>/.htaccess');
    vout('   Evaluated  : per-directory parsing, same-directory effect, parent -> child inheritance, child override.');
    if ($parsed !== null && resultState($parsed) === 'ACTIVE') vout('   Result     : ACTIVE -- Apache parses .htaccess below the DocumentRoot.');
    else vout('   Result     : NOT PROVEN -- inspect the parse probe before trusting capability tests.');
    if ($scope !== null) vout('   Same dir   : ' . resultState($scope) . ' -- ' . $scope['why']);
    if ($nested !== null) vout('   Descendant : ' . resultState($nested) . ' -- ' . $nested['why']);
    vout();

    $placements = [
        ['enabled'=>$testDocroot, 'label'=>'DocumentRoot', 'file'=>$docroot . '/.htaccess'],
        ['enabled'=>$testDomain, 'label'=>'DomainParent', 'file'=>$domainDir . '/.htaccess'],
    ];
    $n = 2;
    foreach ($placements as $p) {
        vout($n . ') ' . $p['label'] . ' placement');
        vout('   File       : ' . $p['file']);
        if (!$p['enabled']) {
            vout('   Result     : NOT TESTED (disabled by --no-root/--no-docroot/--no-domain or root=0/docroot=0/domain=0, or not writable).');
            vout(); $n++; continue;
        }
        $placementResult = null;
        foreach ($results as $r) {
            if (($r['placement'] ?? '') === $p['label'] && ($r['classification'] ?? '') === 'root-placement-active') { $placementResult = $r; break; }
        }
        if ($placementResult !== null) {
            vout('   Result     : ACTIVE -- a temporary Header directive in this exact file changed the probe response.');
            if ($p['label'] === 'DomainParent') {
                vout('   Scope      : IMPORTANT: this file is above httpdocs yet still participates in request traversal for the tested domain.');
                vout('                Settings placed here can therefore affect descendants under httpdocs, subject to directive merge rules.');
            } else {
                vout('   Scope      : Applies at the web DocumentRoot and normally to descendants unless a deeper configuration changes behavior.');
            }
        } else {
            vout('   Result     : NOT PROVEN/INCONCLUSIVE -- no unique placement marker was observed.');
        }
        $tested=[];
        foreach ($results as $r) if (($r['placement'] ?? '') === $p['label'] && ($r['classification'] ?? '') !== 'root-placement-active') $tested[] = $r;
        if ($tested) {
            vout('   Settings evaluated at this placement:');
            foreach ($tested as $r) {
                $shortName=preg_replace('/^\[ROOT [^\]]+\]\s*/', '', (string)$r['name']) ?? (string)$r['name'];
                vout(sprintf('     %-18s %-34s %s', resultState($r), substr($shortName, 0, 34), compactSetting((string)($r['directives'] ?? ''))));
            }
        }
        vout(); $n++;
    }

    if ($testDocroot && $testDomain) {
        $doc=[]; $dom=[];
        foreach ($results as $r) {
            $name=(string)($r['name'] ?? '');
            if (($r['placement'] ?? '') === 'DocumentRoot' && str_starts_with($name, '[ROOT DocumentRoot] ')) {
                $doc[substr($name, strlen('[ROOT DocumentRoot] '))]=resultState($r);
            } elseif (($r['placement'] ?? '') === 'DomainParent' && str_starts_with($name, '[ROOT DomainParent] ')) {
                $dom[substr($name, strlen('[ROOT DomainParent] '))]=resultState($r);
            }
        }
        $common=array_values(array_intersect(array_keys($doc), array_keys($dom)));
        if ($common) {
            $docActive=isset($doc['Header placement marker']) && $doc['Header placement marker']==='ACTIVE';
            $domActive=isset($dom['Header placement marker']) && $dom['Header placement marker']==='ACTIVE';
            $diff=[];
            foreach($common as $name) if($doc[$name] !== $dom[$name]) $diff[]=$name . ': DocumentRoot=' . $doc[$name] . ', DomainParent=' . $dom[$name];
            vout('Root placement comparison:');
            if (!$docActive || !$domActive) {
                vout('   A meaningful divergence comparison requires BOTH placements to be proven ACTIVE.');
                vout('   Current states are shown above, but matching inconclusive states are not evidence of equivalent behavior.');
            } elseif (!$diff) {
                vout('   No divergence was observed for the settings tested at BOTH active root placements.');
                vout('   This does not imply every Apache directive will merge identically; it only covers the probes listed above.');
            } else {
                vout('   DIVERGENCE DETECTED between active DocumentRoot and DomainParent placements:');
                foreach($diff as $d) vout('     - ' . $d);
            }
            vout();
        }
    }
}

function printCapabilityAnalysis(array $results): void
{
    vout('CAPABILITY ANALYSIS');
    vout(str_repeat('-', 78));
    vout('The entries below describe the exact test placement and directive, not merely the module name.');
    vout('REJECTED means the server rejected that valid .htaccess setting at the tested placement;');
    vout('it does not by itself distinguish "module absent" from "AllowOverride/Options policy forbids it".');
    vout();

    $families = ['FileInfo / modules','Indexes','Options','AuthConfig / access control','PHP handler integration','Placement / parsing'];
    foreach ($families as $family) {
        $items = array_values(array_filter($results, static fn($r) => resultFamily($r) === $family && !str_starts_with((string)($r['name'] ?? ''), '[ROOT ')));
        if (!$items) continue;
        vout($family . ':');
        foreach ($items as $r) {
            $state=resultState($r);
            $file=(string)($r['htaccess_path'] ?? '(generated/manual setup)');
            $setting=compactSetting((string)($r['directives'] ?? ($r['setting'] ?? '')));
            vout(sprintf('  %-18s %s', $state, $r['name']));
            vout('    file    : ' . $file);
            vout('    setting : ' . $setting);
            vout('    evidence: ' . $r['why']);
        }
        vout();
    }
}

function printConfigurationHints(array $results, bool $testDocroot, bool $testDomain): void
{
    vout('LIKELY CONFIGURATION PATTERN / PRACTICAL HINTS');
    vout(str_repeat('-', 78));
    vout('These are deductions from behavior. They are NOT a claim about the literal Apache/Plesk config,');
    vout('because shared hosting does not expose the provider\'s <Directory>/AllowOverride directives.');
    vout();

    $fileInfo = ['mod_headers-supported','mod_rewrite-supported','mod_alias-redirect-supported','errordocument-supported','addtype-supported','fallbackresource-supported'];
    if (allClassificationsPresent($results, $fileInfo)) {
        vout('  * FileInfo-style overrides are broadly usable in nested .htaccess files: headers, rewrites,');
        vout('    redirects, custom errors, MIME mappings and FallbackResource all demonstrated behavior.');
        vout('    This is compatible with a broad FileInfo allowance (or AllowOverride All), but does not prove the exact directive.');
    }
    if (findResultByClassification($results, 'directoryindex-supported') !== null) {
        vout('  * DirectoryIndex works, so the Indexes override family is usable at the nested test placement.');
    }
    if (findResultByClassification($results, 'require-supported') !== null && findResultByClassification($results, 'files-container-supported') !== null) {
        vout('  * Apache 2.4 Require and <Files> access control work, consistent with AuthConfig/access-control overrides being permitted.');
    }
    $follow=findResultByClassification($results,'followsymlinks-rejected');
    $owner=findResultByClassification($results,'symlinksifownermatch-supported');
    if ($follow !== null && $owner !== null) {
        vout('  * Options is NOT unrestricted: +FollowSymLinks was rejected while +SymLinksIfOwnerMatch worked.');
        vout('    This is a common managed-hosting pattern: safer same-owner symlinks are allowed while unrestricted symlink following is blocked.');
        vout('    Effective behavior resembles token-restricted Options permissions rather than a blanket "AllowOverride Options".');
    } elseif (findResultByClassification($results,'followsymlinks-supported') !== null) {
        vout('  * +FollowSymLinks worked in the disposable nested directory, so unrestricted symlink following is available there.');
    }
    if (findResultByClassification($results,'options-indexes-enable-supported') !== null && findResultByClassification($results,'options-indexes-disable-supported') !== null) {
        vout('  * Both +Indexes and -Indexes worked in the disposable directory. Directory listing can therefore be explicitly enabled/disabled there.');
    }
    if (findResultByClassification($results,'options-multiviews-supported') !== null) {
        vout('  * -MultiViews is accepted. This is useful for rewrite-heavy applications because MultiViews can otherwise pre-empt URL rewriting.');
    }

    $phpSapi=extractRuntimeValue($results,'WEB_PHP_SAPI');
    $phpVersion=extractRuntimeValue($results,'WEB_PHP_VERSION');
    $backendSoftware=extractRuntimeValue($results,'SERVER_SOFTWARE');
    $runtimeResult=findResultByClassification($results,'web-runtime-detected');
    $httpServer=$runtimeResult !== null ? headerValue($runtimeResult['response'],'server') : null;
    if ($phpSapi !== null || $phpVersion !== null) {
        vout('  * Web PHP observed: ' . ($phpVersion ?? '?') . ' via SAPI ' . ($phpSapi ?? '?') . '.');
    }
    if ($httpServer !== null || $backendSoftware !== null) {
        vout('    HTTP Server header=' . ($httpServer ?? '?') . '; PHP SERVER_SOFTWARE=' . ($backendSoftware ?? '?') . '.');
        if ($httpServer !== null && $backendSoftware !== null && stripos($httpServer,'nginx') !== false && stripos($backendSoftware,'apache') !== false) {
            vout('    This strongly suggests the common Plesk/shared-hosting layout nginx -> Apache: nginx fronts the request,');
            vout('    while Apache behind it evaluates .htaccess. nginx itself does not read .htaccess files.');
        }
    }
    if (findResultByClassification($results,'php_value-effective') !== null) {
        vout('    php_value was behaviorally verified: the requested PHP ini value was visible inside a PHP request.');
    } elseif (findResultByClassification($results,'php_value-accepted-not-effective') !== null) {
        vout('    php_value was accepted by Apache but the requested value was NOT visible in PHP. Treat it as ineffective for PHP configuration.');
        vout('    This pattern is possible when Apache parses legacy directives but PHP is handled by a separate FastCGI/FPM layer.');
    } elseif (findResultByClassification($results,'php_value-not-supported') !== null) {
        vout('    php_value is rejected here. Configure PHP through the hosting PHP/FPM settings or other handler-specific mechanisms instead.');
    }

    if ($testDocroot) {
        $r=null; foreach($results as $x) if(($x['placement']??'')==='DocumentRoot' && ($x['classification']??'')==='root-placement-active') {$r=$x;break;}
        if($r!==null) vout('  * /httpdocs/.htaccess is confirmed active, so DocumentRoot-level rules can govern the whole public tree.');
    }
    if ($testDomain) {
        $r=null; foreach($results as $x) if(($x['placement']??'')==='DomainParent' && ($x['classification']??'')==='root-placement-active') {$r=$x;break;}
        if($r!==null) {
            vout('  * The domain-parent .htaccess ABOVE httpdocs is confirmed active. This is easy to overlook:');
            vout('    a rule there can influence the public tree even though the file itself is not inside the webroot.');
        }
    }
    vout();
}

function printDetailedResultLedger(array $results): void
{
    vout('DETAILED RESULT LEDGER');
    vout(str_repeat('-', 78));
    foreach ($results as $r) {
        $state=resultState($r);
        vout(sprintf('%-18s %s', $state, $r['name']));
        if (!empty($r['placement'])) vout('  placement: ' . $r['placement']);
        if (!empty($r['htaccess_path'])) vout('  file     : ' . $r['htaccess_path']);
        if (!empty($r['directives'])) vout('  setting  : ' . compactSetting((string)$r['directives'], 220));
        if (!empty($r['url'])) vout('  URL      : ' . $r['url']);
        vout('  result   : ' . $r['why'] . ' [' . $r['classification'] . ']');
    }
}

interface HttpClient
{
    /** @return array{url:string,exit_code:int,stderr:string,status:int,effective_url:string,redirect_url:string,raw_headers:string,header_blocks:array,body:string} */
    public function request(string $url, array $headers = [], bool $follow = false): array;
}

/** HTTP client without the curl binary: ext-curl, else PHP streams. Used in the browser mode. */
final class NativeClient implements HttpClient
{
    /** @param string[] $defaultHeaders sent unless a request sets the same header (empty value = send none) */
    public function __construct(private int $timeout, private bool $insecure, private array $defaultHeaders = []) {}

    /** ext-curl usable? (hosts often disable curl_exec while curl_init still exists) */
    public static function hasExtCurl(): bool
    {
        return function_exists('curl_init') && function_exists('curl_exec');
    }

    public static function available(): bool
    {
        return self::hasExtCurl() || (bool)ini_get('allow_url_fopen');
    }

    public function request(string $url, array $headers = [], bool $follow = false): array
    {
        $headers[] = 'Cache-Control: no-cache';
        $headers[] = 'Pragma: no-cache';
        $headers = mergeHeaders($headers, $this->defaultHeaders);

        if (self::hasExtCurl()) {
            $ch = curl_init($url);
            curl_setopt_array($ch, [
                CURLOPT_RETURNTRANSFER => true, CURLOPT_HEADER => true,
                CURLOPT_FOLLOWLOCATION => $follow, CURLOPT_MAXREDIRS => $follow ? 5 : 0,
                CURLOPT_CONNECTTIMEOUT => 8, CURLOPT_TIMEOUT => $this->timeout,
                CURLOPT_HTTPHEADER => $headers,
                CURLOPT_SSL_VERIFYPEER => !$this->insecure, CURLOPT_SSL_VERIFYHOST => $this->insecure ? 0 : 2,
            ]);
            $raw = curl_exec($ch);
            $errno = curl_errno($ch);
            $error = curl_error($ch);
            $info = curl_getinfo($ch);
            $rawHeaders = '';
            $body = '';
            if (is_string($raw)) {
                $hs = (int)($info['header_size'] ?? 0);
                $rawHeaders = substr($raw, 0, $hs);
                $body = substr($raw, $hs);
            }
            return [
                'url' => $url, 'exit_code' => $errno, 'stderr' => $error, 'status' => (int)($info['http_code'] ?? 0),
                'effective_url' => (string)($info['url'] ?? $url), 'redirect_url' => (string)($info['redirect_url'] ?? ''),
                'raw_headers' => $rawHeaders, 'header_blocks' => parseHeaderBlocks($rawHeaders), 'body' => $body,
            ];
        }

        $ctx = stream_context_create([
            'http' => ['method' => 'GET', 'header' => implode("\r\n", $headers), 'ignore_errors' => true,
                       'follow_location' => $follow ? 1 : 0, 'max_redirects' => 5, 'timeout' => $this->timeout],
            'ssl' => ['verify_peer' => !$this->insecure, 'verify_peer_name' => !$this->insecure],
        ]);
        $body = @file_get_contents($url, false, $ctx);
        // PHP 8.4+: http_get_last_response_headers(); $http_response_header is deprecated in 8.5.
        $lines = function_exists('http_get_last_response_headers') ? (http_get_last_response_headers() ?? []) : ($http_response_header ?? []);
        $status = 0;
        $rawHeaders = '';
        foreach ($lines as $line) {   // one block per response (redirects): blank line before each status line
            if (preg_match('~^HTTP/\S+\s+(\d{3})~', $line, $m)) { $status = (int)$m[1]; $rawHeaders .= "\r\n"; }
            $rawHeaders .= $line . "\r\n";
        }
        return [
            'url' => $url, 'exit_code' => $body === false && !$lines ? 1 : 0, 'stderr' => $body === false && !$lines ? 'request failed' : '',
            'status' => $status, 'effective_url' => $url, 'redirect_url' => '',
            'raw_headers' => $rawHeaders, 'header_blocks' => parseHeaderBlocks($rawHeaders), 'body' => (string)$body,
        ];
    }
}

function writeJsonReport(string $target, array $results, array $meta): void
{
    $rows = [];
    $counts = ['OK' => 0, 'FAIL' => 0, 'WARN' => 0, '??' => 0];
    foreach ($results as $r) {
        $label = trim(shortState($r));
        $counts[$label]++;
        $rows[] = [
            'name' => $r['name'], 'state' => $label, 'detail_state' => resultState($r), 'classification' => $r['classification'],
            'reason' => $r['why'], 'placement' => $r['placement'] ?? '', 'http_status' => $r['response']['status'] ?? null,
        ];
    }
    $json = json_encode($meta + ['counts' => $counts, 'results' => $rows], JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_SUBSTITUTE) . "\n";
    if ($target === '-') { echo $json; $GLOBALS['HTA_JSON_DONE'] = true; return; }
    if (@file_put_contents($target, $json) === false) err('Could not write JSON report: ' . $target);
    else out('JSON report written: ' . $target);
}

/** Last result whose name contains $needle (last = a retry such as the {SHA} Basic Auth login wins). */
function findByName(array $results, string $needle): ?array
{
    $found = null;
    foreach ($results as $r) if (stripos((string)$r['name'], $needle) !== false) $found = $r;
    return $found;
}

function printCompactSummary(array $results, bool $docrootTested, bool $domainTested): void
{
    $counts = ['OK' => 0, 'FAIL' => 0, 'WARN' => 0, '??' => 0];
    foreach ($results as $r) $counts[trim(shortState($r))]++;
    out();
    out(str_repeat('=', 78));
    out('SUMMARY');
    out(str_repeat('=', 78));
    out(sprintf('%d tests: %d OK, %d FAIL, %d WARN, %d undecided', count($results), $counts['OK'], $counts['FAIL'], $counts['WARN'], $counts['??']));
    out();

    $active = static function(string $placement) use ($results): bool {
        foreach ($results as $r) if (($r['placement'] ?? '') === $placement && ($r['classification'] ?? '') === 'root-placement-active') return true;
        return false;
    };
    $nested = findResultByClassification($results, 'htaccess-parsed');
    out('.htaccess files are read in:');
    out('  probe folder      : ' . (($nested['ok'] ?? false) ? 'yes' : 'NOT PROVEN'));
    out('  web root          : ' . ($docrootTested ? ($active('DocumentRoot') ? 'yes' : 'NOT PROVEN') : 'not tested'));
    out('  above the web root: ' . ($domainTested ? ($active('DomainParent') ? 'yes' : 'NOT PROVEN') : 'not tested'));
    out();

    $bad = array_filter($results, static fn($r) => trim(shortState($r)) !== 'OK');
    if ($bad) {
        out('Needs attention:');
        foreach ($bad as $r) out('  [' . paint(shortState($r)) . '] ' . $r['name'] . ' - ' . $r['why']);
        out();
    }

    $row = static function(string $label, array $needles) use ($results): void {
        $labels = [];
        foreach ($needles as $n) {
            $r = findByName($results, $n);
            $labels[] = $r === null ? 'not run' : trim(shortState($r));
        }
        $uniq = array_values(array_unique($labels));
        $box = static fn(string $l): string => $l === 'not run' ? $l : '[' . paint(str_pad($l, 4, ' ', STR_PAD_BOTH)) . ']';
        $shown = count($uniq) === 1 ? $box($uniq[0]) : implode(' / ', array_map($box, $labels));
        out(sprintf('  %-34s %s', $label, $shown));
    };
    out('For a PHP web application:');
    $row('Deny rules (Require, <Files>)', ['Authorization: Require all denied', '<Files> container: protected file']);
    $row('Rewrite / front controller', ['mod_rewrite: RewriteCond']);
    $row('Response headers', ['mod_headers']);
    $row('Directory listing off', ['Options -Indexes']);
    $row('Hidden .htaccess and dotfiles', ['.htaccess file not downloadable', 'Dotfile not downloadable']);
    $row('Basic Auth (challenge / login)', ['Basic Auth: login required', 'Basic Auth: valid login']);
    $row('REMOTE_USER visible to PHP', ['Basic Auth: REMOTE_USER']);
    $row('PHP settings: php_value', ['Legacy PHP directive: php_value']);
    $row('PHP settings: .user.ini', ['PHP settings via .user.ini']);
    $pv = findByName($results, 'php_value');
    $ui = findByName($results, '.user.ini');
    if ($pv !== null && !$pv['ok'] && $ui !== null) {
        out('  -> php_value does not work here; ' . ($ui['ok'] ? 'use .user.ini instead.' : 'change PHP settings in the hosting panel.'));
    } elseif ($pv !== null && $pv['ok'] && $ui !== null && !$ui['ok']) {
        out('  -> .user.ini is not read here (normal for mod_php / apache2handler); use php_value instead.');
    }
    $df = findByName($results, 'Dotfile not downloadable');
    if ($df !== null && ($df['classification'] ?? '') === 'dotfile-exposed') {
        out('  -> keep .env, .git, *.sqlite and similar files OUTSIDE the web root, or deny them in .htaccess.');
    }
    $ver = extractRuntimeValue($results, 'WEB_PHP_VERSION');
    if ($ver !== null) out(sprintf('  Web PHP: %s via %s, server software: %s', $ver, extractRuntimeValue($results, 'WEB_PHP_SAPI') ?? '?', extractRuntimeValue($results, 'SERVER_SOFTWARE') ?? '?'));
    $pdo = extractRuntimeValue($results, 'PDO_DRIVERS');
    if ($pdo !== null) out('  Web PHP database drivers (PDO): ' . ($pdo !== '' ? $pdo : '(none)') . (str_contains(',' . $pdo . ',', ',sqlite,') ? '' : '  -> NO pdo_sqlite'));
    out();
    out('OK = works, FAIL = rejected by the server, WARN = accepted without effect, ?? = undecided (try --verbose).');
}

final class CurlClient implements HttpClient
{
    public function __construct(
        private string $curl,
        private int $timeout,
        private bool $insecure,
        private string $scratchDir,
        private string $token,
        private array $defaultHeaders = [],
    ) {}

    public function request(string $url, array $headers = [], bool $follow = false): array
    {
        $hdrFile = @tempnam($this->scratchDir, 'hdr_');
        $bodyFile = @tempnam($this->scratchDir, 'body_');
        if ($hdrFile === false || $bodyFile === false) {
            throw new RuntimeException('Could not create curl scratch files.');
        }

        $headers[] = 'Cache-Control: no-cache';
        $headers[] = 'Pragma: no-cache';
        $headers[] = 'X-HTA-Probe-Client: ' . $this->token;
        $headers = mergeHeaders($headers, $this->defaultHeaders);

        $parts = [
            escapeshellarg($this->curl),
            '--silent', '--show-error',
            '--connect-timeout', '8',
            '--max-time', (string)$this->timeout,
            '-D', escapeshellarg($hdrFile),
            '-o', escapeshellarg($bodyFile),
            '-w', escapeshellarg('%{http_code}\n%{url_effective}\n%{redirect_url}\n'),
        ];
        if ($this->insecure) $parts[] = '--insecure';
        if ($follow) {
            $parts[] = '--location';
            $parts[] = '--max-redirs';
            $parts[] = '5';
        } else {
            $parts[] = '--max-redirs';
            $parts[] = '0';
        }
        foreach ($headers as $h) {
            $parts[] = '-H';
            $parts[] = escapeshellarg($h);
        }
        $parts[] = escapeshellarg($url);
        $cmd = implode(' ', $parts);

        $stdout = '';
        $stderr = '';
        $exitCode = 127;

        if (function_exists('proc_open') && !disabledFunction('proc_open')) {
            $spec = [1 => ['pipe', 'w'], 2 => ['pipe', 'w']];
            $proc = @proc_open($cmd, $spec, $pipes);
            if (is_resource($proc)) {
                $stdout = stream_get_contents($pipes[1]) ?: '';
                $stderr = stream_get_contents($pipes[2]) ?: '';
                fclose($pipes[1]); fclose($pipes[2]);
                $exitCode = proc_close($proc);
            }
        }
        if ($exitCode === 127 && function_exists('exec') && !disabledFunction('exec')) {
            $errFile = tempnam($this->scratchDir, 'curlerr_');
            if ($errFile === false) {
                @unlink($hdrFile); @unlink($bodyFile);
                throw new RuntimeException('Could not create curl stderr scratch file.');
            }
            $lines = [];
            @exec($cmd . ' 2>' . escapeshellarg($errFile), $lines, $exitCode);
            $stdout = implode("\n", $lines);
            $stderr = (string)@file_get_contents($errFile);
            @unlink($errFile);
        }
        if ($exitCode === 127) {
            @unlink($hdrFile); @unlink($bodyFile);
            throw new RuntimeException('PHP proc_open() and exec() are unavailable or could not start curl CLI.');
        }

        $rawHeaders = (string)@file_get_contents($hdrFile);
        $body = (string)@file_get_contents($bodyFile);
        @unlink($hdrFile); @unlink($bodyFile);

        $meta = explode("\n", trim($stdout));
        $status = isset($meta[0]) && ctype_digit(trim($meta[0])) ? (int)trim($meta[0]) : 0;
        $effective = trim($meta[1] ?? '');
        $redirect = trim($meta[2] ?? '');

        return [
            'url' => $url,
            'exit_code' => $exitCode,
            'stderr' => trim($stderr),
            'status' => $status,
            'effective_url' => $effective,
            'redirect_url' => $redirect,
            'raw_headers' => $rawHeaders,
            'header_blocks' => parseHeaderBlocks($rawHeaders),
            'body' => $body,
        ];
    }
}

final class RootHtaccessTransaction
{
    private ?array $active = null;
    private bool $hadRestoreProblem = false;

    public function __construct(
        private string $backupDir,
        private string $token,
    ) {
        mkdirp($this->backupDir);
        @chmod($this->backupDir, 0700);
    }

    public function backupDir(): string { return $this->backupDir; }
    public function hadRestoreProblem(): bool { return $this->hadRestoreProblem; }
    public function isActive(): bool { return $this->active !== null; }

    /** Append a uniquely delimited block, execute one callback, then remove only that exact block. */
    public function withBlock(string $path, string $label, string $directives, callable $callback): mixed
    {
        if ($this->active !== null) {
            throw new RuntimeException('Internal error: nested root .htaccess transactions are not supported.');
        }
        $parent = dirname($path);
        if (!is_dir($parent) || !is_writable($parent)) {
            throw new RuntimeException("Cannot safely modify root .htaccess; directory is not writable: {$parent}");
        }

        clearstatcache(true, $path);
        $originallyExisted = is_file($path);
        $fh = @fopen($path, 'c+b');
        if (!is_resource($fh)) throw new RuntimeException("Cannot open root .htaccess for transactional test: {$path}");
        if (!@flock($fh, LOCK_EX)) {
            @fclose($fh);
            throw new RuntimeException("Cannot acquire exclusive lock on: {$path}");
        }
        $activated = false;
        $this->maskSignals(true);   // no Ctrl-C/SIGTERM between "block written" and "block registered for restore"

        try {
            rewind($fh);
            $original = stream_get_contents($fh);
            if ($original === false) throw new RuntimeException("Cannot read: {$path}");
            $st = fstat($fh) ?: [];
            $mode = isset($st['mode']) ? ($st['mode'] & 07777) : null;

            $safeLabel = preg_replace('/[^A-Za-z0-9_.-]+/', '_', $label) ?: 'root_probe';
            $nonce = bin2hex(random_bytes(6));
            $backup = $this->backupDir . '/' . $safeLabel . '_' . $nonce . '.bak';
            $meta = $backup . '.json';
            if (@file_put_contents($backup, $original, LOCK_EX) === false) throw new RuntimeException("Could not create root .htaccess backup: {$backup}");
            @chmod($backup, 0600);
            $metadata = [
                'path' => $path, 'label' => $label, 'created_at' => date(DATE_ATOM),
                'originally_existed' => $originallyExisted, 'original_size' => strlen($original),
                'original_sha256' => hash('sha256', $original),
                'original_mode_octal' => $mode === null ? null : sprintf('%04o', $mode), 'backup' => $backup,
            ];
            @file_put_contents($meta, json_encode($metadata, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES) . "\n", LOCK_EX);
            @chmod($meta, 0600);

            $markerId = $this->token . '-' . $nonce;
            $prefix = ($original !== '' && !str_ends_with($original, "\n")) ? "\n" : '';
            $insert = $prefix . "# BEGIN HTA_PROBE {$markerId}\n" . rtrim($directives, "\r\n") . "\n" . "# END HTA_PROBE {$markerId}\n";

            if (fseek($fh, 0, SEEK_END) !== 0) throw new RuntimeException("Could not seek: {$path}");
            $written = fwrite($fh, $insert);
            if ($written === false || $written !== strlen($insert)) throw new RuntimeException("Could not append complete probe block to: {$path}");
            fflush($fh);
            if (function_exists('fsync')) @fsync($fh);
            if ($mode !== null) @chmod($path, $mode);

            $this->active = [
                'path' => $path, 'fh' => $fh, 'insert' => $insert, 'backup' => $backup, 'meta' => $meta,
                'originally_existed' => $originallyExisted, 'original' => $original,
            ];
            $activated = true;

            try {
                $this->maskSignals(false);   // a signal from here on is handled by emergencyRestore()
                return $callback($backup, $meta);
            } finally {
                $this->restoreActive(false);
            }
        } catch (Throwable $e) {
            if ($activated) {
                if ($this->active !== null) $this->restoreActive(false);
            } else {
                if (isset($original) && is_string($original)) {
                    @rewind($fh); @ftruncate($fh, 0);
                    if ($original !== '') @fwrite($fh, $original);
                    @fflush($fh); if (function_exists('fsync')) @fsync($fh);
                }
                @flock($fh, LOCK_UN); @fclose($fh);
                if (!$originallyExisted && is_file($path) && @filesize($path) === 0) @unlink($path);
                $this->maskSignals(false);
            }
            throw $e;
        }
    }

    public function emergencyRestore(): void { if ($this->active !== null) $this->restoreActive(true); }

    /** CLI with pcntl: defer SIGINT/SIGTERM/SIGHUP while the live file is half-way through a change. */
    private function maskSignals(bool $block): void
    {
        if (PHP_SAPI === 'cli' && function_exists('pcntl_sigprocmask') && defined('SIGTERM')) {
            @pcntl_sigprocmask($block ? SIG_BLOCK : SIG_UNBLOCK, [SIGINT, SIGTERM, SIGHUP]);
        }
    }

    private function restoreActive(bool $emergency): void
    {
        if ($this->active === null) return;
        $this->maskSignals(true);
        try { $this->restoreActiveMasked($emergency); } finally { $this->maskSignals(false); }
    }

    private function restoreActiveMasked(bool $emergency): void
    {
        $a = $this->active;
        $this->active = null;
        $fh = $a['fh']; $path = $a['path']; $insert = $a['insert']; $restored = false;
        try {
            clearstatcache(true, $path);
            rewind($fh);
            $current = stream_get_contents($fh);
            if ($current === false) throw new RuntimeException("Could not read during restoration: {$path}");
            $pos = strpos($current, $insert);
            if ($pos === false) {
                $this->hadRestoreProblem = true;
                err('ROOT RESTORE WARNING: probe block was no longer present in ' . $path);
                err('ROOT RESTORE WARNING: refusing to overwrite the file; backup kept at ' . $a['backup']);
                return;
            }
            if (strpos($current, $insert, $pos + 1) !== false) {
                $this->hadRestoreProblem = true;
                err('ROOT RESTORE WARNING: probe block appeared more than once in ' . $path);
                err('ROOT RESTORE WARNING: refusing ambiguous automatic restoration; backup kept at ' . $a['backup']);
                return;
            }
            $new = substr($current, 0, $pos) . substr($current, $pos + strlen($insert));
            rewind($fh);
            if (!ftruncate($fh, 0)) throw new RuntimeException("Could not truncate during restoration: {$path}");
            $n = fwrite($fh, $new);
            if ($n === false || $n !== strlen($new)) throw new RuntimeException("Could not restore complete content: {$path}");
            fflush($fh); if (function_exists('fsync')) @fsync($fh);
            $restored = true;
        } catch (Throwable $e) {
            $this->hadRestoreProblem = true;
            err('ROOT RESTORE ERROR: ' . $e->getMessage());
            err('ROOT RESTORE ERROR: backup kept at ' . $a['backup']);
        } finally {
            @flock($fh, LOCK_UN); @fclose($fh);
        }

        if ($restored && !$a['originally_existed']) {
            clearstatcache(true, $path);
            if (is_file($path) && @filesize($path) === 0) @unlink($path);
        }
        if ($emergency && $restored) err('Emergency root .htaccess restoration completed: ' . $path);
    }

    public function cleanupBackupsIfSafe(): void
    {
        if ($this->active !== null || $this->hadRestoreProblem) return;
        rrmdir($this->backupDir);
    }
}

final class ProbeRunner
{
    private array $results = [];
    private int $counter = 0;

    public function __construct(
        private string $docroot,
        private string $probeName,
        private string $baseUrl,
        private HttpClient $http,
        private LogWatcher $logs,
        private string $token,
        private int $bodyPreviewBytes,
    ) {}

    public function dir(string $name): string
    {
        return $this->docroot . '/' . $this->probeName . '/' . $name;
    }

    public function url(string $suffix = ''): string
    {
        $base = rtrim($this->baseUrl, '/') . '/' . rawurlencode($this->probeName);
        if ($suffix !== '') $base .= '/' . ltrim($suffix, '/');
        return $base;
    }

    public function write(string $relative, string $content): void
    {
        writeFileStrict($this->docroot . '/' . $this->probeName . '/' . ltrim($relative, '/'), $content);
    }

    public function htaccess(string $dir, string $contents): void
    {
        $path = $this->dir($dir) . '/.htaccess';
        writeFileStrict($path, rtrim($contents) . "\n");
    }

    public function probe(
        string $name,
        string $description,
        string $dir,
        string $htaccess,
        string $requestSuffix,
        callable $assert,
        array $requestHeaders = [],
        bool $follow = false,
        bool $showBodyAlways = false,
    ): array {
        $this->counter++;
        vout();
        vout(str_repeat('=', 78));
        vout(sprintf('[%02d] %s', $this->counter, $name));
        vout($description);
        vout('Directory : ' . $this->dir($dir));
        vout('URL       : ' . $this->url($requestSuffix));
        vout('.htaccess :');
        foreach (explode("\n", rtrim($htaccess)) as $line) vout('    ' . $line);

        $this->htaccess($dir, $htaccess);
        $this->logs->mark();
        $url = $this->url($requestSuffix);
        $sep = str_contains($url, '?') ? '&' : '?';
        $url .= $sep . '_hta_probe=' . rawurlencode($this->token) . '&n=' . $this->counter;
        $response = $this->http->request($url, $requestHeaders, $follow);
        // Give buffered hosting logs a brief chance to flush.
        usleep(120000);
        $logDelta = $this->logs->delta();

        [$ok, $why, $classification] = $assert($response);
        $result = [
            'name' => $name,
            'ok' => (bool)$ok,
            'why' => (string)$why,
            'classification' => (string)$classification,
            'response' => $response,
            'description' => $description,
            'placement' => 'NestedProbe',
            'htaccess_path' => $this->dir($dir) . '/.htaccess',
            'directives' => $htaccess,
            'url' => $url,
            'scope' => $this->dir($dir) . ' and descendants',
        ];
        $this->results[] = $result;
        reportLine($result);

        vout('Result    : ' . resultState($result) . ($classification !== '' ? " [{$classification}]" : ''));
        vout('Reason    : ' . $why);
        vout('HTTP      : ' . $response['status'] . ' (curl exit ' . $response['exit_code'] . ')');
        $server = headerValue($response, 'server');
        $ctype = headerValue($response, 'content-type');
        $location = headerValue($response, 'location');
        $encoding = headerValue($response, 'content-encoding');
        if ($server !== null) vout('Server    : ' . $server);
        if ($ctype !== null) vout('Type      : ' . $ctype);
        if ($encoding !== null) vout('Encoding  : ' . $encoding);
        if ($location !== null) vout('Location  : ' . $location);
        if ($response['redirect_url'] !== '') vout('curl redir: ' . $response['redirect_url']);
        if ($response['stderr'] !== '') vout('curl err  : ' . $response['stderr']);

        $interesting = ['x-hta-probe', 'x-hta-parent', 'x-hta-child', 'x-hta-scope', 'expires', 'cache-control', 'allow'];
        foreach ($interesting as $h) {
            $v = headerValue($response, $h);
            if ($v !== null) vout('Header    : ' . $h . ': ' . $v);
        }

        if ($showBodyAlways || !$ok || $response['status'] >= 400) {
            $preview = printableBody($response['body'], $this->bodyPreviewBytes);
            if ($preview !== '') {
                vout('Body      :');
                foreach (explode("\n", $preview) as $line) vout('    ' . $line);
            }
        }

        if ($logDelta) {
            vout('New log lines:');
            foreach ($logDelta as $file => $data) {
                vout('  --- ' . $file . ' ---');
                foreach (explode("\n", rtrim($data)) as $line) vout('    ' . $line);
            }
        } elseif (!$ok || $response['status'] >= 500) {
            vout('New log lines: none captured from readable known log files.');
        }

        return $result;
    }

    public function manualProbe(
        string $name,
        string $description,
        string $requestSuffix,
        callable $assert,
        array $requestHeaders = [],
        bool $follow = false,
    ): array {
        $this->counter++;
        vout();
        vout(str_repeat('=', 78));
        vout(sprintf('[%02d] %s', $this->counter, $name));
        vout($description);
        vout('URL       : ' . $this->url($requestSuffix));

        $this->logs->mark();
        $url = $this->url($requestSuffix);
        $sep = str_contains($url, '?') ? '&' : '?';
        $url .= $sep . '_hta_probe=' . rawurlencode($this->token) . '&n=' . $this->counter;
        $response = $this->http->request($url, $requestHeaders, $follow);
        usleep(120000);
        $logDelta = $this->logs->delta();
        [$ok, $why, $classification] = $assert($response);
        $result = ['name'=>$name, 'ok'=>(bool)$ok, 'why'=>(string)$why, 'classification'=>(string)$classification, 'response'=>$response, 'description'=>$description, 'placement'=>'Control/fixture', 'htaccess_path'=>null, 'directives'=>'', 'url'=>$url];
        $this->results[] = $result;
        reportLine($result);

        vout('Result    : ' . resultState($result) . ($classification !== '' ? " [{$classification}]" : ''));
        vout('Reason    : ' . $why);
        vout('HTTP      : ' . $response['status'] . ' (curl exit ' . $response['exit_code'] . ')');
        $server = headerValue($response, 'server');
        $ctype = headerValue($response, 'content-type');
        if ($server !== null) vout('Server    : ' . $server);
        if ($ctype !== null) vout('Type      : ' . $ctype);
        foreach (['x-hta-probe', 'x-hta-parent', 'x-hta-child', 'x-hta-scope', 'expires', 'cache-control', 'location', 'content-encoding'] as $h) {
            $v = headerValue($response, $h);
            if ($v !== null) vout('Header    : ' . $h . ': ' . $v);
        }
        if (!$ok || $response['status'] >= 400 || $name === 'Web PHP runtime') {
            $preview = printableBody($response['body'], $this->bodyPreviewBytes);
            if ($preview !== '') {
                vout('Body      :');
                foreach (explode("\n", $preview) as $line) vout('    ' . $line);
            }
        }
        if ($logDelta) {
            vout('New log lines:');
            foreach ($logDelta as $file => $data) {
                vout('  --- ' . $file . ' ---');
                foreach (explode("\n", rtrim($data)) as $line) vout('    ' . $line);
            }
        }
        return $result;
    }

    public function results(): array { return $this->results; }
}

function runRootProbe(
    string $name, string $description, string $placementLabel, string $htaccessPath,
    string $directives, string $url, HttpClient $http, LogWatcher $logs,
    RootHtaccessTransaction $tx, callable $assert, int $bodyPreviewBytes,
    array $requestHeaders = [],
): array {
    vout(); vout(str_repeat('=', 78)); vout('[ROOT] ' . $name); vout($description);
    vout('Placement : ' . $placementLabel); vout('File      : ' . $htaccessPath); vout('URL       : ' . $url);
    vout('Temporary block:'); foreach (explode("\n", rtrim($directives)) as $line) vout('    ' . $line);
    $response = null; $logs->mark();
    $tx->withBlock($htaccessPath, $placementLabel . '_' . $name, $directives, static function() use (&$response, $http, $url, $requestHeaders): void {
        $response = $http->request($url, $requestHeaders, false);
    });
    usleep(120000); $logDelta = $logs->delta();
    if (!is_array($response)) throw new RuntimeException('Internal error: root probe produced no HTTP response.');
    [$ok, $why, $classification] = $assert($response);
    $result = ['name'=>'[ROOT ' . $placementLabel . '] ' . $name, 'ok'=>(bool)$ok, 'why'=>(string)$why, 'classification'=>(string)$classification, 'response'=>$response, 'description'=>$description, 'placement'=>$placementLabel, 'htaccess_path'=>$htaccessPath, 'directives'=>$directives, 'url'=>$url];
    reportLine($result);
    vout('Result    : ' . resultState($result) . ($classification !== '' ? " [{$classification}]" : ''));
    vout('Reason    : ' . $why); vout('HTTP      : ' . $response['status'] . ' (curl exit ' . $response['exit_code'] . ')');
    foreach (['server','content-type','location','x-hta-root','x-hta-root-option','content-encoding'] as $h) {
        $v = headerValue($response, $h); if ($v !== null) vout('Header    : ' . $h . ': ' . $v);
    }
    if ($response['stderr'] !== '') vout('curl err  : ' . $response['stderr']);
    if (!$ok || $response['status'] >= 400) {
        $preview = printableBody($response['body'], $bodyPreviewBytes);
        if ($preview !== '') { vout('Body      :'); foreach (explode("\n", $preview) as $line) vout('    ' . $line); }
    }
    if ($logDelta) {
        vout('New log lines:'); foreach ($logDelta as $file => $data) { vout('  --- ' . $file . ' ---'); foreach (explode("\n", rtrim($data)) as $line) vout('    ' . $line); }
    } elseif (!$ok || $response['status'] >= 500) vout('New log lines: none captured from readable known log files.');
    return $result;
}

function rootUrl(string $baseUrl, string $probeName, string $relative, string $token): string
{
    $url = rtrim($baseUrl, '/') . '/' . rawurlencode($probeName) . '/' . ltrim($relative, '/');
    $sep = str_contains($url, '?') ? '&' : '?';
    return $url . $sep . '_hta_root_probe=' . rawurlencode($token);
}

// ---------- options: CLI arguments or browser query ----------
$isWeb = PHP_SAPI !== 'cli';
$jsonTarget = null;
$GLOBALS['HTA_JSON_DONE'] = false;
if ($isWeb) {
    // Nothing is output and nothing is touched before the token check. Tokens shorter than 16
    // characters are refused so a placeholder like "1" cannot open the page.
    $given = isset($_GET['token']) && is_string($_GET['token']) ? $_GET['token'] : '';
    if (strlen(HTA_WEB_TOKEN) < 16 || !hash_equals(HTA_WEB_TOKEN, $given)) {
        http_response_code(403);
        header('Content-Type: text/plain; charset=utf-8');
        header('Cache-Control: no-store');
        echo "Forbidden. Set HTA_WEB_TOKEN in this file (at least 16 characters, openssl rand -hex 16) and call ?token=...\n";
        exit;
    }
    // Disabled functions are undefined since PHP 8 (calling them throws), hence function_exists().
    if (function_exists('ini_set')) { @ini_set('display_errors', '0'); @ini_set('zlib.output_compression', '0'); }
    if (function_exists('set_time_limit')) @set_time_limit(600);
    if (function_exists('ignore_user_abort')) @ignore_user_abort(true);   // client abort must not interrupt a root .htaccess transaction
    set_error_handler(static fn(int $no, string $msg): bool => true);   // open_basedir warnings etc.
    if (($_GET['format'] ?? '') === 'json') $jsonTarget = '-';
    header('Content-Type: ' . ($jsonTarget === '-' ? 'application/json' : 'text/plain') . '; charset=utf-8');
    header('Cache-Control: no-store');
    header('X-Accel-Buffering: no');
    header('X-Robots-Tag: noindex');
    while (ob_get_level() > 0 && @ob_end_flush());   // stops at a buffer that cannot be removed
    ob_implicit_flush(true);
}
// JSON on stdout must stay valid even after a PHP fatal error (e.g. max_execution_time).
register_shutdown_function(static function() use (&$jsonTarget): void {
    if ($jsonTarget !== '-' || $GLOBALS['HTA_JSON_DONE']) return;
    $e = error_get_last();
    $msg = $GLOBALS['HTA_FATAL'] ?? (($e !== null && in_array($e['type'], [E_ERROR, E_PARSE, E_CORE_ERROR, E_COMPILE_ERROR], true)) ? $e['message'] : 'aborted before the report was complete');
    echo json_encode(['version' => SCRIPT_VERSION, 'error' => $msg], JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE), "\n";
});

try {
    $target = null; $keep = false; $insecure = false; $timeout = 15; $rootTimeout = 5; $bodyPreviewBytes = 500;
    $wantDocroot = true; $wantDomain = true; $colorMode = 'auto'; $docrootArg = null; $verbose = false; $authArg = null;
    if ($isWeb) {
        $verbose = flagParam('verbose', false);
        $keep = flagParam('keep', false);
        $insecure = flagParam('insecure', false);
        $wantDocroot = flagParam('root', true) && flagParam('docroot', true);
        $wantDomain = flagParam('root', true) && flagParam('domain', true);
        if (isset($_GET['timeout']) && is_string($_GET['timeout'])) $timeout = max(2, min(120, (int)$_GET['timeout']));
    } else {
        $args = $argv; array_shift($args);
        if (!$args || in_array('--help', $args, true) || in_array('-h', $args, true)) usage($args ? 0 : 2);
        foreach ($args as $arg) {
            if ($arg === '--keep') $keep = true;
            elseif ($arg === '--insecure') $insecure = true;
            elseif ($arg === '--verbose' || $arg === '-v') $verbose = true;
            elseif ($arg === '--no-root') { $wantDocroot = false; $wantDomain = false; }
            elseif ($arg === '--no-docroot') $wantDocroot = false;
            elseif ($arg === '--no-domain') $wantDomain = false;
            elseif ($arg === '--no-color') $colorMode = 'never';
            elseif ($arg === '--color') $colorMode = 'always';
            elseif (str_starts_with($arg, '--json=')) {
                $jsonTarget = substr($arg, 7);
                if ($jsonTarget === '') usageError('--json needs a file name or - (stdout).');
            } elseif (str_starts_with($arg, '--docroot=')) {
                $docrootArg = substr($arg, 10);
                if (trim($docrootArg, '/ ') === '') usageError('--docroot needs a folder name.');
            } elseif (str_starts_with($arg, '--auth=')) {
                $authArg = substr($arg, 7);
                if (!str_contains($authArg, ':')) usageError('--auth must be USER:PASS.');
            } elseif (str_starts_with($arg, '--timeout=')) {
                $timeout = ctype_digit(substr($arg, 10)) ? (int)substr($arg, 10) : 0;
                if ($timeout < 2 || $timeout > 120) usageError('--timeout must be between 2 and 120 seconds.');
            } elseif (str_starts_with($arg, '--root-timeout=')) {
                $rootTimeout = ctype_digit(substr($arg, 15)) ? (int)substr($arg, 15) : 0;
                if ($rootTimeout < 2 || $rootTimeout > 15) usageError('--root-timeout must be between 2 and 15 seconds.');
            } elseif (str_starts_with($arg, '-')) usageError("unknown option: {$arg}");
            elseif ($target === null) $target = $arg;
            else usageError("unexpected argument: {$arg} (only one domain/base URL)");
        }
        if ($target === null) usageError('missing <domain-or-base-url>.');
        try { $baseUrl = normalizeBaseUrl($target); } catch (RuntimeException $e) { usageError($e->getMessage()); }
    }
    if ($verbose) $bodyPreviewBytes = 2000;
    $GLOBALS['HTA_VERBOSE'] = $verbose;
    $GLOBALS['HTA_QUIET'] = ($jsonTarget === '-');
    $GLOBALS['HTA_COLOR'] = !$isWeb && $jsonTarget !== '-' && ($colorMode === 'always' || ($colorMode === 'auto' && (string)getenv('NO_COLOR') === ''
        && (function_exists('stream_isatty') ? @stream_isatty(STDOUT) : (function_exists('posix_isatty') && @posix_isatty(STDOUT)))));

    // Where are we? $probeBase = folder that receives the probe folder (and is reachable at $baseUrl),
    // $docroot = real document root (root .htaccess test), $domainDir = folder above it.
    if ($isWeb) {
        $probeBase = rtrim(str_replace('\\', '/', __DIR__), '/');
        $docroot = rtrim(str_replace('\\', '/', (string)(@realpath((string)($_SERVER['DOCUMENT_ROOT'] ?? '')) ?: $probeBase)), '/');
        $https = (!empty($_SERVER['HTTPS']) && strtolower((string)$_SERVER['HTTPS']) !== 'off')
            || strtolower((string)($_SERVER['HTTP_X_FORWARDED_PROTO'] ?? '')) === 'https'
            || (string)($_SERVER['SERVER_PORT'] ?? '') === '443';
        $host = (string)($_SERVER['HTTP_HOST'] ?? $_SERVER['SERVER_NAME'] ?? 'localhost');
        $urlDir = rtrim(str_replace('\\', '/', dirname((string)($_SERVER['SCRIPT_NAME'] ?? '/'))), '/');
        $baseUrl = ($https ? 'https' : 'http') . '://' . $host . $urlDir;
        $domainDir = dirname($docroot);
    } else {
        $domainDir = rtrim(str_replace('\\', '/', __DIR__), '/');
        $docroot = $docrootArg === null ? $domainDir . '/httpdocs'
            : rtrim(str_starts_with($docrootArg, '/') ? $docrootArg : $domainDir . '/' . $docrootArg, '/');
        $probeBase = $docroot;
    }
    if (!is_dir($probeBase)) throw new RuntimeException("Expected web folder not found: {$probeBase}" . ($isWeb ? '' : ' (use --docroot=NAME; place this script in the folder that contains the web root)'));
    if (!is_writable($probeBase)) throw new RuntimeException("Web folder is not writable: {$probeBase}");

    // Root placement usable = folder writable AND (no .htaccess yet, or the existing one writable).
    $rootWritable = static fn(string $dir): bool => pathReachable($dir) && is_dir($dir) && is_writable($dir)
        && (!is_file($dir . '/.htaccess') || is_writable($dir . '/.htaccess'));
    $testDocrootHtaccess = $wantDocroot && $rootWritable($docroot);
    $testDomainHtaccess = $wantDomain && $domainDir !== $docroot && $rootWritable($domainDir);
    $rootNotes = []; $notes = [];
    if ($wantDocroot && !$testDocrootHtaccess) $rootNotes[] = "web root .htaccess not tested: {$docroot} or its .htaccess is not writable or blocked by open_basedir";
    if ($wantDomain && !$testDomainHtaccess) $rootNotes[] = "above-web-root .htaccess not tested: {$domainDir} or its .htaccess is not writable or blocked by open_basedir" . ($isWeb ? ' (run the CLI version one level above the web root for this)' : '');
    // Backups of the live root .htaccess must not land in the public tree; first usable folder wins.
    $backupBase = null;
    if ($testDocrootHtaccess || $testDomainHtaccess) {
        $candidates = [$testDomainHtaccess ? $domainDir : null, sys_get_temp_dir(), (string)ini_get('upload_tmp_dir'), $docroot !== $domainDir ? $domainDir : null];
        foreach ($candidates as $c) {
            if ($c !== null && $c !== '' && pathReachable($c) && @is_dir($c) && @is_writable($c)) { $backupBase = rtrim($c, '/'); break; }
        }
        if ($backupBase === null) {
            $rootNotes[] = 'root .htaccess tests skipped: no writable folder outside the web root for the backup (temp dir blocked by open_basedir?)';
            $testDocrootHtaccess = $testDomainHtaccess = false;
        }
    }

    $curl = $isWeb ? null : commandExists('curl');
    if ($curl === null && !NativeClient::available()) throw new RuntimeException('Neither the curl binary, ext-curl nor allow_url_fopen is available; no way to send HTTP requests.');
    $forward = [];
    if ($isWeb) {   // a site that is already behind Basic Auth would otherwise answer our self-requests with 401
        $auth = $_SERVER['HTTP_AUTHORIZATION'] ?? $_SERVER['REDIRECT_HTTP_AUTHORIZATION'] ?? null;
        if ($auth === null && isset($_SERVER['PHP_AUTH_USER'])) $auth = 'Basic ' . base64_encode($_SERVER['PHP_AUTH_USER'] . ':' . ($_SERVER['PHP_AUTH_PW'] ?? ''));
        if ($auth !== null) $forward[] = 'Authorization: ' . $auth;
        elseif (($_SERVER['REMOTE_USER'] ?? $_SERVER['REDIRECT_REMOTE_USER'] ?? '') !== '') {
            $notes[] = 'this page is behind a login, but PHP cannot see the password (Authorization header not passed to PHP; "CGIPassAuth On" in .htaccess helps), so the probe requests will get HTTP 401';
        }
    } elseif ($authArg !== null) {
        $forward[] = 'Authorization: Basic ' . base64_encode($authArg);
    }

    $token = bin2hex(random_bytes(6));
    $probeName = '__hta_probe_' . date('Ymd_His') . '_' . $token;
    $probeRoot = $probeBase . '/' . $probeName;
    mkdirp($probeRoot);

    $cleaned = false;
    $cleanup = static function() use (&$cleaned, $keep, $probeRoot): void {
        if ($cleaned) return;
        $cleaned = true;
        if (!$keep) rrmdir($probeRoot);
    };
    register_shutdown_function($cleanup);

    $makeClient = static function(int $t) use ($curl, $insecure, $probeRoot, $token, $forward): HttpClient {
        return $curl !== null ? new CurlClient($curl, $t, $insecure, $probeRoot, $token, $forward) : new NativeClient($t, $insecure, $forward);
    };
    $logs = new LogWatcher($domainDir);
    $http = $makeClient($timeout);
    $rootHttp = $makeClient($rootTimeout);
    $runner = new ProbeRunner($probeBase, $probeName, $baseUrl, $http, $logs, $token, $bodyPreviewBytes);
    $rootTx = null;
    if ($testDocrootHtaccess || $testDomainHtaccess) {
        $rootTx = new RootHtaccessTransaction($backupBase . '/.hta_probe_backups_' . $token, $token);
        register_shutdown_function(static function() use ($rootTx): void { $rootTx->emergencyRestore(); $rootTx->cleanupBackupsIfSafe(); });
    }
    // CLI: Ctrl-C/SIGTERM/SIGHUP -> exit() -> shutdown functions restore the root .htaccess and remove the probe folder.
    $signalsHandled = false;
    if (!$isWeb && function_exists('pcntl_async_signals') && function_exists('pcntl_signal')) {
        pcntl_async_signals(true);
        foreach (['SIGINT'=>130, 'SIGTERM'=>143, 'SIGHUP'=>129] as $sigName=>$exitCode) {
            if (defined($sigName)) $signalsHandled = @pcntl_signal(constant($sigName), static function() use ($exitCode,$sigName): void {
                err("Received {$sigName}; restoring any active root .htaccess block and cleaning up before exit.");
                exit($exitCode);
            }) || $signalsHandled;
        }
    }
    if ($rootTx !== null && !$isWeb && !$signalsHandled) {
        $notes[] = 'PHP has no pcntl here: if this run is killed (Ctrl-C) during a root test, the probe block stays in the .htaccess; the original is backed up in ' . $rootTx->backupDir();
    }

    out('.htaccess capability probe v' . SCRIPT_VERSION . ' (' . ($isWeb ? 'browser' : 'cli') . ', PHP ' . PHP_VERSION . ' ' . PHP_SAPI . ')');
    out('Probe URL : ' . rtrim($baseUrl, '/') . '/' . $probeName . '/');
    out('Root tests: web root ' . ($testDocrootHtaccess ? 'yes' : 'no') . ', above web root ' . ($testDomainHtaccess ? 'yes' : 'no')
        . ($rootTx !== null ? ' (one temporary block at a time, backed up first, removed right after)' : ''));
    foreach ($rootNotes as $note) out('[SKIP] ' . $note);
    foreach ($notes as $note) out('[NOTE] ' . $note);
    vout('Script dir    : ' . $domainDir);
    vout('Web root      : ' . $docroot);
    vout('Probe dir     : ' . $probeRoot);
    vout('HTTP client   : ' . ($curl !== null ? $curl : (NativeClient::hasExtCurl() ? 'ext-curl' : 'PHP streams')));
    vout('TLS verify    : ' . ($insecure ? 'DISABLED' : 'enabled'));
    vout('Readable logs : ' . ($logs->files ? implode(', ', $logs->files) : '(none detected)'));
    foreach ([$docroot . '/.htaccess', $domainDir . '/.htaccess'] as $existingHta) {
        if (@is_file($existingHta)) {
            if (str_contains((string)@file_get_contents($existingHta), '# BEGIN HTA_PROBE ')) {
                out('[WARN] ' . $existingHta . ' still contains a "# BEGIN HTA_PROBE" block from an interrupted run; remove it (up to "# END HTA_PROBE").');
            }
            $sz = @filesize($existingHta);
            vout('Existing hta  : ' . $existingHta . ' (' . ($sz === false ? '?' : $sz) . ' bytes, sha256 ' . (@hash_file('sha256', $existingHta) ?: '?') . ')');
        } else {
            vout('Existing hta  : ' . $existingHta . ' (none)');
        }
    }
    out();

    // Shared fixtures.
    $runner->write('baseline.txt', "BASELINE_OK {$token}\n");

    // 1. Baseline HTTP reachability, no .htaccess involved.
    $runner->manualProbe(
        'Baseline static file',
        'Confirms that the generated directory is reachable through the supplied public URL before testing Apache overrides.',
        'baseline.txt',
        static function(array $r) use ($token): array {
            $ok = $r['status'] === 200 && str_contains($r['body'], "BASELINE_OK {$token}");
            return [$ok, $ok ? 'Static file reached with expected unique body.' : ('The URL-to-docroot mapping or HTTP access failed (HTTP ' . $r['status'] . ($r['status'] === 401 ? ', the site is behind a login: CLI --auth=USER:PASS, browser: open this page logged in' : '') . '); later tests may be meaningless.'), $ok ? 'baseline-ok' : 'baseline-failed'];
        }
    );

    // 2. Web-runtime information (direct physical PHP file, no .htaccess needed).
    $runner->write('runtime.php', <<<'PHPFILE'
<?php
header('Content-Type: text/plain; charset=utf-8');
echo 'WEB_PHP_VERSION=' . PHP_VERSION . "\n";
echo 'WEB_PHP_SAPI=' . PHP_SAPI . "\n";
echo 'SERVER_SOFTWARE=' . ($_SERVER['SERVER_SOFTWARE'] ?? '') . "\n";
echo 'DOCUMENT_ROOT=' . ($_SERVER['DOCUMENT_ROOT'] ?? '') . "\n";
echo 'SCRIPT_FILENAME=' . ($_SERVER['SCRIPT_FILENAME'] ?? '') . "\n";
if (function_exists('apache_get_modules')) {
    echo 'APACHE_MODULES=' . implode(',', apache_get_modules()) . "\n";
}
echo 'PDO_DRIVERS=' . (class_exists('PDO') ? implode(',', PDO::getAvailableDrivers()) : '') . "\n";
PHPFILE
    );
    $runner->manualProbe(
        'Web PHP runtime',
        'Shows the PHP SAPI seen through HTTP (for example fpm-fcgi vs apache2handler) and basic server mapping. No environment variables or secrets are dumped.',
        'runtime.php',
        static function(array $r): array {
            $ok = $r['status'] === 200 && str_contains($r['body'], 'WEB_PHP_VERSION=') && str_contains($r['body'], 'WEB_PHP_SAPI=');
            return [$ok, $ok ? 'Runtime endpoint executed successfully. See body below for SAPI/server details.' : 'Runtime endpoint did not execute as expected.', $ok ? 'web-runtime-detected' : 'web-runtime-failed'];
        }
    );

    // 3. Definitive parse test using deliberately invalid directive.
    $runner->write('parse-test/file.txt', "PARSE_TEST_FILE {$token}\n");
    $runner->probe(
        'Is .htaccess parsed at all?',
        'Uses a deliberately invalid directive only inside the disposable test directory. HTTP 500 strongly confirms Apache is reading .htaccess here; HTTP 200 means it is probably ignored.',
        'parse-test',
        "HTAProbeThisDirectiveMustNotExist On",
        'parse-test/file.txt',
        static function(array $r): array {
            if ($r['status'] === 500) return [true, 'Deliberately invalid directive caused HTTP 500, so .htaccess is being parsed in this directory.', 'htaccess-parsed'];
            if ($r['status'] === 200) return [false, 'Request still succeeded despite an invalid directive; .htaccess appears to be ignored here.', 'htaccess-possibly-ignored'];
            return [false, 'Expected 500 (parsed) or 200 (ignored), but got a different status.', 'unexpected-status'];
        }
    );

    // 3. mod_headers / FileInfo.
    $runner->write('headers/file.txt', "HEADERS_BODY {$token}\n");
    $runner->probe(
        'mod_headers: Header set',
        'Tests whether the Header directive is available in .htaccess (typically FileInfo override + mod_headers).',
        'headers',
        'Header set X-HTA-Probe "' . $token . '"',
        'headers/file.txt',
        static function(array $r) use ($token): array {
            $v = headerValue($r, 'x-hta-probe');
            if ($r['status'] === 200 && $v === $token) return [true, 'Custom response header is present with the expected value.', 'mod_headers-supported'];
            if ($r['status'] === 500) return [false, 'Directive caused HTTP 500; mod_headers may be unavailable or Header may be forbidden by AllowOverride.', 'header-directive-rejected'];
            return [false, 'Request did not contain the expected X-HTA-Probe header.', 'header-not-effective'];
        }
    );

    // 4. mod_rewrite basic.
    $runner->write('rewrite/target.txt', "REWRITE_OK {$token}\n");
    $runner->write('rewrite/virtual', "REWRITE_SOURCE_SHOULD_BE_REPLACED {$token}\n");
    $runner->probe(
        'mod_rewrite: internal rewrite',
        'Tests RewriteEngine + RewriteRule in per-directory context. The pattern intentionally omits the directory prefix.',
        'rewrite',
        "RewriteEngine On\nRewriteRule ^virtual$ target.txt [L]",
        'rewrite/virtual',
        static function(array $r) use ($token): array {
            if ($r['status'] === 200 && str_contains($r['body'], "REWRITE_OK {$token}")) return [true, 'Virtual path was internally rewritten to target.txt.', 'mod_rewrite-supported'];
            if ($r['status'] === 500) return [false, 'Rewrite directives caused HTTP 500; inspect error log for forbidden/unknown directive.', 'rewrite-rejected'];
            return [false, 'Rewrite did not yield the expected target body.', 'rewrite-not-effective'];
        },
        [], false, true
    );

    // 5. END flag.
    $runner->write('rewrite-end/target.txt', "REWRITE_END_OK {$token}\n");
    $runner->write('rewrite-end/virtual', "REWRITE_END_SOURCE_SHOULD_BE_REPLACED {$token}\n");
    $runner->probe(
        'mod_rewrite: [END] flag',
        'Checks support for the Apache 2.4 END flag, useful for terminating per-directory rewrite processing.',
        'rewrite-end',
        "RewriteEngine On\nRewriteRule ^virtual$ target.txt [END]",
        'rewrite-end/virtual',
        static function(array $r) use ($token): array {
            if ($r['status'] === 200 && str_contains($r['body'], "REWRITE_END_OK {$token}")) return [true, '[END] is accepted and the rewrite worked.', 'rewrite-end-supported'];
            if ($r['status'] === 500) return [false, '[END] or rewrite configuration was rejected.', 'rewrite-end-rejected'];
            return [false, 'Expected rewritten content was not returned.', 'rewrite-end-not-effective'];
        }
    );

    // 6. RewriteCond + query string preservation.
    $runner->write('rewrite-cond/target.php', <<<'PHPFILE'
<?php
header('Content-Type: text/plain');
echo 'COND_OK id=' . ($_GET['id'] ?? '') . ' foo=' . ($_GET['foo'] ?? '') . ' go=' . ($_GET['go'] ?? '') . "\n";
PHPFILE
    );
    $runner->write('rewrite-cond/item-123', "REWRITE_COND_SOURCE_SHOULD_BE_REPLACED {$token}\n");
    $runner->probe(
        'mod_rewrite: RewriteCond + captures + QSA',
        'Exercises a more realistic front-controller style rule with RewriteCond, a capture group, PHP handoff, and query-string append.',
        'rewrite-cond',
        "RewriteEngine On\nRewriteCond %{QUERY_STRING} (^|&)go=1(&|$)\nRewriteRule ^item-([0-9]+)$ target.php?id=$1 [L,QSA]",
        'rewrite-cond/item-123?go=1&foo=bar',
        static function(array $r): array {
            $ok = $r['status'] === 200 && str_contains($r['body'], 'COND_OK id=123 foo=bar go=1');
            return [$ok, $ok ? 'RewriteCond, capture substitution, PHP execution and QSA all behaved as expected.' : 'Expected PHP output with id=123, foo=bar and go=1 was not returned.', $ok ? 'rewrite-advanced-supported' : 'rewrite-advanced-failed'];
        },
        [], false, true
    );

    // 7. Redirect / mod_alias.
    $runner->write('redirect/target.txt', "REDIRECT_TARGET {$token}\n");
    $runner->write('redirect/source', "REDIRECT_SOURCE_SHOULD_NOT_BE_SERVED {$token}\n");
    $redirectSourcePath = parse_url($runner->url('redirect/source'), PHP_URL_PATH) ?: ('/' . $probeName . '/redirect/source');
    $redirectTargetUrl = $runner->url('redirect/target.txt');
    $runner->probe(
        'mod_alias: Redirect 302',
        'Tests Redirect independently from mod_rewrite.',
        'redirect',
        'Redirect 302 ' . $redirectSourcePath . ' ' . $redirectTargetUrl,
        'redirect/source',
        static function(array $r) use ($redirectTargetUrl): array {
            $loc = headerValue($r, 'location');
            $ok = $r['status'] === 302 && $loc !== null && str_starts_with($loc, $redirectTargetUrl);
            if ($ok) return [true, 'Received HTTP 302 with the expected Location.', 'mod_alias-redirect-supported'];
            if ($r['status'] === 500) return [false, 'Redirect directive caused HTTP 500.', 'redirect-rejected'];
            return [false, 'Did not receive the expected 302 redirect.', 'redirect-not-effective'];
        }
    );

    // 8. DirectoryIndex / Indexes override class.
    $runner->write('directory-index/unusual-index.marker', "DIRECTORY_INDEX_OK {$token}\n");
    $runner->probe(
        'DirectoryIndex',
        'Tests whether DirectoryIndex can be set from a nested .htaccess (Indexes override class).',
        'directory-index',
        'DirectoryIndex unusual-index.marker',
        'directory-index/',
        static function(array $r) use ($token): array {
            $ok = $r['status'] === 200 && str_contains($r['body'], "DIRECTORY_INDEX_OK {$token}");
            if ($ok) return [true, 'Custom DirectoryIndex was served.', 'directoryindex-supported'];
            if ($r['status'] === 500) return [false, 'DirectoryIndex caused HTTP 500; likely forbidden or unavailable.', 'directoryindex-rejected'];
            return [false, 'Directory did not serve the configured custom index.', 'directoryindex-not-effective'];
        },
        [], false, true
    );

    // 9. Options +Indexes.
    $runner->write('indexes-on/a-probe.txt', "A {$token}\n");
    $runner->write('indexes-on/b-probe.txt', "B {$token}\n");
    $runner->probe(
        'Options +Indexes',
        'Attempts to enable directory listing only in the random disposable directory.',
        'indexes-on',
        'Options +Indexes',
        'indexes-on/',
        static function(array $r): array {
            $hasListing = $r['status'] === 200 && str_contains($r['body'], 'a-probe.txt') && str_contains($r['body'], 'b-probe.txt');
            if ($hasListing) return [true, 'Directory listing was enabled and contains the probe files.', 'options-indexes-enable-supported'];
            if ($r['status'] === 500) return [false, 'Options +Indexes was rejected by server configuration.', 'options-indexes-enable-rejected'];
            if ($r['status'] === 403) return [false, 'Directory listing remains forbidden; +Indexes may be disallowed or AutoIndex unavailable.', 'indexes-still-forbidden'];
            return [false, 'No recognizable generated directory listing was returned.', 'indexes-enable-inconclusive'];
        }
    );

    // 10. Options -Indexes.
    $runner->write('indexes-off/visible.txt', "VISIBLE {$token}\n");
    $runner->probe(
        'Options -Indexes',
        'Disables directory listing in the test directory. A 403 for the directory itself indicates the directive was accepted/effective.',
        'indexes-off',
        'Options -Indexes',
        'indexes-off/',
        static function(array $r): array {
            if ($r['status'] === 403) return [true, 'Directory request is forbidden as expected with -Indexes.', 'options-indexes-disable-supported'];
            if ($r['status'] === 500) return [false, 'Options -Indexes was rejected.', 'options-indexes-disable-rejected'];
            return [false, 'Expected 403 for a directory without an index file.', 'options-indexes-disable-inconclusive'];
        }
    );

    // 11. Options -MultiViews.
    $runner->write('multiviews/file.txt', "MULTIVIEWS_TEST {$token}\n");
    $runner->probe(
        'Options -MultiViews',
        'Checks whether a commonly used rewrite-safety setting is accepted in .htaccess.',
        'multiviews',
        'Options -MultiViews',
        'multiviews/file.txt',
        static function(array $r) use ($token): array {
            if ($r['status'] === 200 && str_contains($r['body'], "MULTIVIEWS_TEST {$token}")) return [true, 'Options -MultiViews was accepted without breaking access.', 'options-multiviews-supported'];
            if ($r['status'] === 500) return [false, 'Options -MultiViews was rejected.', 'options-multiviews-rejected'];
            return [false, 'Unexpected response while testing -MultiViews.', 'options-multiviews-inconclusive'];
        }
    );

    // 12. Require all denied / AuthConfig.
    $runner->write('require-deny/secret.txt', "SHOULD_NOT_BE_PUBLIC {$token}\n");
    $runner->probe(
        'Authorization: Require all denied',
        'Tests Apache 2.4 authorization directives from .htaccess (typically AuthConfig override class).',
        'require-deny',
        'Require all denied',
        'require-deny/secret.txt',
        static function(array $r): array {
            if ($r['status'] === 403) return [true, 'Access was denied with HTTP 403 as expected.', 'require-supported'];
            if ($r['status'] === 500) return [false, 'Require directive was rejected/forbidden.', 'require-rejected'];
            if ($r['status'] === 200) return [false, 'File remained accessible; Require did not take effect.', 'require-not-effective'];
            return [false, 'Unexpected status while testing Require.', 'require-inconclusive'];
        }
    );

    // 13. Files container + Require.
    $runner->write('files-container/public.txt', "PUBLIC_OK {$token}\n");
    $runner->write('files-container/secret.txt', "SECRET {$token}\n");
    $runner->htaccess('files-container', "<Files \"secret.txt\">\n    Require all denied\n</Files>");
    $runner->manualProbe(
        '<Files> container: public file',
        'First half of a selective file-denial test.',
        'files-container/public.txt',
        static function(array $r) use ($token): array {
            $ok = $r['status'] === 200 && str_contains($r['body'], "PUBLIC_OK {$token}");
            return [$ok, $ok ? 'Public sibling remains accessible.' : 'Public sibling was unexpectedly blocked/broken.', $ok ? 'files-container-public-ok' : 'files-container-public-failed'];
        }
    );
    $runner->manualProbe(
        '<Files> container: protected file',
        'Second half of the selective file-denial test; secret.txt should be denied.',
        'files-container/secret.txt',
        static function(array $r): array {
            $ok = $r['status'] === 403;
            return [$ok, $ok ? 'secret.txt was selectively denied with 403.' : 'secret.txt was not selectively denied.', $ok ? 'files-container-supported' : 'files-container-failed'];
        }
    );

    // 14. ErrorDocument.
    $runner->write('error-doc/custom404.txt', "CUSTOM_404_OK {$token}\n");
    $runner->write('error-doc/trigger', "ERROR_TRIGGER_SOURCE {$token}\n");
    $custom404Path = parse_url($runner->url('error-doc/custom404.txt'), PHP_URL_PATH) ?: ('/' . $probeName . '/error-doc/custom404.txt');
    $runner->probe(
        'ErrorDocument 404',
        'Checks whether a nested .htaccess can select a custom 404 body while preserving the 404 status. A rewrite generates the 404 from a physical trigger file to avoid parent front-controller interference.',
        'error-doc',
        "ErrorDocument 404 " . $custom404Path . "\nRewriteEngine On\nRewriteRule ^trigger$ - [R=404,L]",
        'error-doc/trigger',
        static function(array $r) use ($token): array {
            $ok = $r['status'] === 404 && str_contains($r['body'], "CUSTOM_404_OK {$token}");
            if ($ok) return [true, 'Custom error body was served and HTTP status remained 404.', 'errordocument-supported'];
            if ($r['status'] === 500) return [false, 'ErrorDocument configuration caused HTTP 500.', 'errordocument-rejected'];
            return [false, 'Expected custom 404 marker was not returned.', 'errordocument-not-effective'];
        },
        [], false, true
    );

    // 15. AddType / mod_mime.
    $runner->write('addtype/sample.htafoo', "ADDTYPE_OK {$token}\n");
    $runner->probe(
        'mod_mime: AddType',
        'Tests setting a custom MIME type from .htaccess.',
        'addtype',
        'AddType application/x-hta-probe .htafoo',
        'addtype/sample.htafoo',
        static function(array $r): array {
            $ct = strtolower((string)headerValue($r, 'content-type'));
            $ok = $r['status'] === 200 && str_contains($ct, 'application/x-hta-probe');
            if ($ok) return [true, 'Custom MIME type appears in Content-Type.', 'addtype-supported'];
            if ($r['status'] === 500) return [false, 'AddType was rejected/forbidden.', 'addtype-rejected'];
            return [false, 'Response did not use the configured MIME type.', 'addtype-not-effective'];
        }
    );

    // 16. Expires headers.
    $runner->write('expires/file.txt', "EXPIRES_OK {$token}\n");
    $runner->probe(
        'mod_expires: ExpiresActive/ExpiresDefault',
        'Tests whether mod_expires directives are available from .htaccess.',
        'expires',
        "ExpiresActive On\nExpiresDefault \"access plus 1 hour\"",
        'expires/file.txt',
        static function(array $r): array {
            $exp = headerValue($r, 'expires');
            if ($r['status'] === 200 && $exp !== null) return [true, 'Expires header is present.', 'mod_expires-supported'];
            if ($r['status'] === 500) return [false, 'Expires directives caused HTTP 500; module may be absent or directives forbidden.', 'mod_expires-rejected'];
            return [false, 'No Expires header observed.', 'mod_expires-not-effective'];
        }
    );

    // 17. Deflate compression.
    $runner->write('deflate/file.txt', str_repeat("DEFLATE_TEST_{$token}_abcdefghijklmnopqrstuvwxyz\n", 200));
    $runner->probe(
        'mod_deflate: AddOutputFilterByType DEFLATE',
        'Requests a compressible text file with Accept-Encoding: gzip and checks Content-Encoding.',
        'deflate',
        'AddOutputFilterByType DEFLATE text/plain',
        'deflate/file.txt',
        static function(array $r): array {
            $enc = strtolower((string)headerValue($r, 'content-encoding'));
            if ($r['status'] === 200 && str_contains($enc, 'gzip')) return [true, 'Server returned gzip-encoded content.', 'mod_deflate-supported'];
            if ($r['status'] === 500) return [false, 'DEFLATE directive was rejected/forbidden.', 'mod_deflate-rejected'];
            return [false, 'No gzip Content-Encoding observed; compression may be unavailable, disabled, or handled elsewhere.', 'mod_deflate-inconclusive'];
        },
        ['Accept-Encoding: gzip']
    );

    // 18. FallbackResource.
    $runner->write('fallback/fallback.txt', "FALLBACK_OK {$token}\n");
    $fallbackPath = parse_url($runner->url('fallback/fallback.txt'), PHP_URL_PATH) ?: ('/' . $probeName . '/fallback/fallback.txt');
    $runner->probe(
        'FallbackResource',
        'Tests Apache core/mod_dir fallback routing, useful as a simpler alternative to some rewrite front controllers.',
        'fallback',
        'FallbackResource ' . $fallbackPath,
        'fallback/nonexistent-route',
        static function(array $r) use ($token): array {
            $ok = $r['status'] === 200 && str_contains($r['body'], "FALLBACK_OK {$token}");
            if ($ok) return [true, 'Missing path was served by the configured fallback resource.', 'fallbackresource-supported'];
            if ($r['status'] === 500) return [false, 'FallbackResource was rejected/forbidden.', 'fallbackresource-rejected'];
            return [false, 'Fallback resource did not handle the missing path.', 'fallbackresource-not-effective'];
        },
        [], false, true
    );

    // 19. Nested .htaccess inheritance / placement, based on headers.
    $runner->write('scope/parent.txt', "SCOPE_PARENT {$token}\n");
    $runner->write('scope/child/child.txt', "SCOPE_CHILD {$token}\n");
    $runner->htaccess('scope', "Header set X-HTA-Parent \"{$token}\"\nHeader set X-HTA-Scope \"parent\"");
    $runner->htaccess('scope/child', "Header set X-HTA-Child \"{$token}\"\nHeader set X-HTA-Scope \"child\"");
    $runner->manualProbe(
        'Placement: parent .htaccess applies to same directory',
        'Checks the parent-level marker before testing inheritance to a deeper directory.',
        'scope/parent.txt',
        static function(array $r) use ($token): array {
            $ok = $r['status'] === 200 && headerValue($r, 'x-hta-parent') === $token && headerValue($r, 'x-hta-scope') === 'parent';
            return [$ok, $ok ? 'Parent .htaccess affects a file in its own directory.' : 'Parent header markers were not observed.', $ok ? 'scope-parent-supported' : 'scope-parent-failed'];
        }
    );
    $runner->manualProbe(
        'Placement: nested inheritance + child override',
        'Requests a file one level deeper. Expected: parent marker inherited, child marker added, and same-name scope header set to child.',
        'scope/child/child.txt',
        static function(array $r) use ($token): array {
            $parent = headerValue($r, 'x-hta-parent');
            $child = headerValue($r, 'x-hta-child');
            $scope = headerValue($r, 'x-hta-scope');
            $ok = $r['status'] === 200 && $parent === $token && $child === $token && $scope !== null && str_contains($scope, 'child');
            return [$ok, $ok ? 'Parent configuration propagated into the subdirectory and child configuration was also applied.' : "Observed parent=" . ($parent ?? '(none)') . ", child=" . ($child ?? '(none)') . ", scope=" . ($scope ?? '(none)'), $ok ? 'nested-htaccess-supported' : 'nested-htaccess-inconclusive'];
        }
    );

    // 20. Legacy mod_php php_value probe, with behavioral verification.
    $runner->write('php-value/check.php', <<<'PHPVALUECHECK'
<?php
header('Content-Type: text/plain; charset=utf-8');
echo 'PHP_VALUE_MEMORY_LIMIT=' . ini_get('memory_limit') . "\n";
PHPVALUECHECK
    );
    $runner->probe(
        'Legacy PHP directive: php_value',
        'Behaviorally tests php_value, not just syntax acceptance. The .htaccess requests memory_limit=173M and PHP echoes the effective value.',
        'php-value',
        'php_value memory_limit 173M',
        'php-value/check.php',
        static function(array $r): array {
            if ($r['status'] === 500) return [false, 'php_value was rejected by Apache/server policy (HTTP 500).', 'php_value-not-supported'];
            if ($r['status'] === 200 && preg_match('/^PHP_VALUE_MEMORY_LIMIT=173M$/m', $r['body'])) return [true, 'php_value was accepted and PHP reported memory_limit=173M, so the setting is behaviorally effective.', 'php_value-effective'];
            if ($r['status'] === 200 && preg_match('/^PHP_VALUE_MEMORY_LIMIT=(.*)$/m', $r['body'], $m)) return [false, 'Apache accepted the request, but PHP reported memory_limit=' . trim($m[1]) . ' instead of 173M; php_value is not effective for this handler.', 'php_value-accepted-not-effective'];
            return [false, 'Unexpected response; php_value behavior could not be classified cleanly.', 'php_value-inconclusive'];
        }
    );

    // 21/22. Symlink-related Options, only if PHP can create a local symlink.
    $runner->write('symlink-options/target.txt', "SYMLINK_TARGET {$token}\n");
    $symlinkPath = $runner->dir('symlink-options') . '/link.txt';
    $symlinkMade = function_exists('symlink') && @symlink('target.txt', $symlinkPath);
    if ($symlinkMade) {
        $runner->probe(
            'Options +FollowSymLinks',
            'Tests whether this specific Options token is allowed. The symlink points only to a file inside the disposable probe directory.',
            'symlink-options',
            'Options +FollowSymLinks',
            'symlink-options/link.txt',
            static function(array $r) use ($token): array {
                if ($r['status'] === 200 && str_contains($r['body'], "SYMLINK_TARGET {$token}")) return [true, 'FollowSymLinks is accepted and local symlink traversal works.', 'followsymlinks-supported'];
                if ($r['status'] === 500) return [false, 'Options +FollowSymLinks was rejected by server policy.', 'followsymlinks-rejected'];
                return [false, 'Directive may be accepted, but the symlink was not served successfully.', 'followsymlinks-inconclusive'];
            }
        );
        $runner->probe(
            'Options +SymLinksIfOwnerMatch',
            'Tests the more restrictive symlink option on the same local same-owner link.',
            'symlink-options',
            'Options +SymLinksIfOwnerMatch',
            'symlink-options/link.txt',
            static function(array $r) use ($token): array {
                if ($r['status'] === 200 && str_contains($r['body'], "SYMLINK_TARGET {$token}")) return [true, 'SymLinksIfOwnerMatch is accepted and the same-owner symlink works.', 'symlinksifownermatch-supported'];
                if ($r['status'] === 500) return [false, 'Options +SymLinksIfOwnerMatch was rejected.', 'symlinksifownermatch-rejected'];
                return [false, 'Directive may be accepted, but the symlink was not served successfully.', 'symlinksifownermatch-inconclusive'];
            }
        );
    } else {
        out('[SKIP] Symlink option probes: PHP could not create a local symbolic link.');
    }


    // 23. Files that must never be downloadable.
    $runner->write('dotfiles/.env', "DOTFILE_SECRET {$token}\n");
    $runner->manualProbe(
        'Dotfile not downloadable (.env)',
        'Requests a file whose name starts with a dot in a folder without any .htaccess. Some hosts serve these unless a rule blocks them.',
        'dotfiles/.env',
        static function(array $r) use ($token): array {
            if (in_array($r['status'], [403, 404], true)) return [true, 'Dot files are blocked by default (HTTP ' . $r['status'] . ').', 'dotfile-hidden'];
            if ($r['status'] === 200 && str_contains($r['body'], "DOTFILE_SECRET {$token}")) return [false, 'Dot files are served. Add <FilesMatch "^\.">Require all denied</FilesMatch> to your .htaccess.', 'dotfile-exposed'];
            return [false, 'Unexpected HTTP status ' . $r['status'] . '.', 'dotfile-inconclusive'];
        }
    );
    $runner->manualProbe(
        '.htaccess file not downloadable',
        'Requests one of the probe .htaccess files directly. It must not be served as text.',
        'headers/.htaccess',
        static function(array $r): array {
            if (in_array($r['status'], [403, 404], true)) return [true, '.htaccess is not served (HTTP ' . $r['status'] . ').', 'htaccess-file-hidden'];
            if ($r['status'] === 200 && str_contains($r['body'], 'Header set')) return [false, '.htaccess content is downloadable.', 'htaccess-file-exposed'];
            return [false, 'Unexpected HTTP status ' . $r['status'] . '.', 'htaccess-file-inconclusive'];
        }
    );

    // 24. Basic Auth with AuthUserFile, and what PHP sees of the user.
    $baDir = $runner->dir('basic-auth');
    $baPass = 'probe-' . $token;
    $runner->write('basic-auth/secret.txt', "BASIC_AUTH_SECRET {$token}\n");
    $runner->write('basic-auth/.htpasswd', 'probe:' . password_hash($baPass, PASSWORD_BCRYPT) . "\n");
    $runner->write('basic-auth/whoami.php', <<<'PHPWHO'
<?php
header('Content-Type: text/plain; charset=utf-8');
foreach (['REMOTE_USER', 'REDIRECT_REMOTE_USER', 'PHP_AUTH_USER'] as $k) echo $k . '=' . ($_SERVER[$k] ?? '(unset)') . "\n";
echo 'AUTH_HEADER=' . ((isset($_SERVER['HTTP_AUTHORIZATION']) || isset($_SERVER['REDIRECT_HTTP_AUTHORIZATION'])) ? 'present' : '(unset)') . "\n";
PHPWHO
    );
    $baHta = "AuthType Basic\nAuthName \"HTA probe\"\nAuthUserFile \"{$baDir}/.htpasswd\"\nRequire valid-user";
    $baAuth = ['Authorization: Basic ' . base64_encode('probe:' . $baPass)];
    $runner->probe(
        'Basic Auth: login required',
        'AuthType Basic with an AuthUserFile inside the probe folder. A request without credentials must get 401.',
        'basic-auth', $baHta, 'basic-auth/secret.txt',
        static function(array $r): array {
            if ($r['status'] === 401) return [true, 'Request without credentials was challenged with 401.', 'basicauth-challenge-supported'];
            if ($r['status'] === 500) return [false, 'AuthType/AuthUserFile caused HTTP 500 (directive not allowed or mod_auth_basic missing).', 'basicauth-rejected'];
            return [false, 'Request without credentials was not challenged (HTTP ' . $r['status'] . ').', 'basicauth-not-effective'];
        },
        ['Authorization:']
    );
    $baRes = $runner->results();
    $baChallengeOk = (bool)end($baRes)['ok'];   // without a 401 challenge a 200 proves nothing about the login
    $loginAssert = static function(array $r) use ($token, $baChallengeOk): array {
        if ($r['status'] === 200 && str_contains($r['body'], "BASIC_AUTH_SECRET {$token}")) {
            return $baChallengeOk ? [true, 'Valid user and password were accepted.', 'basicauth-login-supported']
                : [false, 'File was served, but Basic Auth was not enforced at all (see previous test).', 'basicauth-login-inconclusive'];
        }
        if ($r['status'] === 401) return [false, 'Valid credentials were refused (the hash type may not be supported).', 'basicauth-login-rejected'];
        return [false, 'Unexpected HTTP status ' . $r['status'] . '.', 'basicauth-login-inconclusive'];
    };
    $runner->probe('Basic Auth: valid login accepted (bcrypt)', 'Same rules; the request carries the right user and password, stored as bcrypt hash.', 'basic-auth', $baHta, 'basic-auth/secret.txt', $loginAssert, $baAuth);
    $baRes = $runner->results();
    if ($baChallengeOk && !end($baRes)['ok']) {
        $runner->write('basic-auth/.htpasswd', 'probe:{SHA}' . base64_encode(sha1($baPass, true)) . "\n");
        $runner->probe('Basic Auth: valid login accepted ({SHA})', 'Retry with the older {SHA} hash type.', 'basic-auth', $baHta, 'basic-auth/secret.txt', $loginAssert, $baAuth);
    }
    $runner->probe(
        'Basic Auth: REMOTE_USER visible to PHP',
        'After a valid login a PHP script reads the user name. Applications that trust REMOTE_USER need this.',
        'basic-auth', $baHta, 'basic-auth/whoami.php',
        static function(array $r): array {
            $v = [];
            foreach (['REMOTE_USER', 'REDIRECT_REMOTE_USER', 'PHP_AUTH_USER'] as $k) if (preg_match('/^' . $k . '=(.*)$/m', $r['body'], $m)) $v[$k] = trim($m[1]);
            if ($r['status'] === 200 && (($v['REMOTE_USER'] ?? '') === 'probe' || ($v['REDIRECT_REMOTE_USER'] ?? '') === 'probe')) return [true, 'PHP sees REMOTE_USER=probe.', 'remote-user-supported'];
            if ($r['status'] === 200 && ($v['PHP_AUTH_USER'] ?? '') === 'probe') return [false, 'REMOTE_USER is not set, only PHP_AUTH_USER; apps must read that instead.', 'remote-user-not-effective'];
            if ($r['status'] === 200) return [false, 'PHP does not see the logged-in user (REMOTE_USER unset); the Authorization header may be dropped before PHP.', 'remote-user-not-effective'];
            return [false, 'Unexpected HTTP status ' . $r['status'] . '.', 'remote-user-inconclusive'];
        },
        $baAuth, false, true
    );

    // 25. .user.ini (the usual replacement for php_value on FastCGI hosts).
    $runner->write('user-ini/.user.ini', "memory_limit=171M\n");
    $runner->write('user-ini/check.php', <<<'PHPUSERINI'
<?php
header('Content-Type: text/plain; charset=utf-8');
echo 'USER_INI_MEMORY_LIMIT=' . ini_get('memory_limit') . "\n";
PHPUSERINI
    );
    $runner->manualProbe(
        'PHP settings via .user.ini',
        'A .user.ini asks for memory_limit=171M and PHP echoes the effective value. This is the usual way to change PHP settings per folder on FastCGI hosts.',
        'user-ini/check.php',
        static function(array $r): array {
            if ($r['status'] === 200 && preg_match('/^USER_INI_MEMORY_LIMIT=171M$/m', $r['body'])) return [true, '.user.ini is applied.', 'user_ini-effective'];
            if ($r['status'] === 200 && preg_match('/^USER_INI_MEMORY_LIMIT=(.*)$/m', $r['body'], $m)) return [false, '.user.ini was ignored (memory_limit=' . trim($m[1]) . '); it may be disabled or cached (user_ini.cache_ttl, default 300 s).', 'user_ini-not-effective'];
            return [false, 'Unexpected HTTP status ' . $r['status'] . '.', 'user_ini-inconclusive'];
        }
    );

    // Root-level placement tests. One short-lived block is appended at a time.
    $rootResults = [];
    if (($testDocrootHtaccess || $testDomainHtaccess) && $rootTx !== null) {
        $placements=[];
        if ($testDocrootHtaccess) $placements[]=['DocumentRoot',$docroot . '/.htaccess','docroot'];
        if ($testDomainHtaccess) $placements[]=['DomainParent',$domainDir . '/.htaccess','domain'];
        foreach ($placements as [$placementLabel,$htaPath,$slug]) {
            vout(); vout(str_repeat('#',78)); vout('ROOT-LEVEL TESTS: ' . $placementLabel . ' => ' . $htaPath); vout(str_repeat('#',78));
          try {

            $headerFile="root-tests/root_header_{$slug}_{$token}.txt"; $runner->write($headerFile,"ROOT_HEADER_BODY {$placementLabel} {$token}\n");
            $headerValueExpected="{$slug}-{$token}";
            $r=runRootProbe('Header placement marker',
                'Proves whether this exact .htaccess placement is traversed and allows Header. The marker exists only for this one short-lived request transaction.',
                $placementLabel,$htaPath,"Header always set X-HTA-Root \"{$headerValueExpected}\"",
                rootUrl($baseUrl,$probeName,$headerFile,$token),$rootHttp,$logs,$rootTx,
                static function(array $resp) use($headerValueExpected): array {
                    $v=headerValue($resp,'x-hta-root');
                    if($resp['status']===200 && $v===$headerValueExpected) return [true,'Unique root placement header was observed.','root-placement-active'];
                    if($resp['status']===500) return [false,'Root-level Header block caused HTTP 500; inspect error log for AllowOverride restrictions.','root-header-rejected'];
                    return [false,'Expected unique root placement header was not observed; this parent may not participate in request traversal.','root-placement-inconclusive'];
                },$bodyPreviewBytes);
            $rootResults[]=$r; $placementActive=$r['ok'];

            $denyFile="root-tests/root_deny_{$slug}_{$token}.txt"; $runner->write($denyFile,"ROOT_DENY_BODY {$placementLabel} {$token}\n");
            $deny="<Files \"" . basename($denyFile) . "\">\n    Require all denied\n</Files>";
            $rootResults[]=runRootProbe('Require all denied in <Files>',
                'Tests AuthConfig/access-control behavior at this exact placement while targeting one unique filename.',
                $placementLabel,$htaPath,$deny,rootUrl($baseUrl,$probeName,$denyFile,$token),$rootHttp,$logs,$rootTx,
                static function(array $resp): array {
                    if($resp['status']===403) return [true,'Unique test file was denied with HTTP 403.','root-require-supported'];
                    if($resp['status']===500) return [false,'Require/<Files> caused HTTP 500 at this placement.','root-require-rejected'];
                    return [false,'Unique test file was not denied as expected.','root-require-not-effective'];
                },$bodyPreviewBytes);

            $ext='.htaproberoot'.$token; $mimeFile="root-tests/root_mime_{$slug}_{$token}{$ext}";
            $runner->write($mimeFile,"ROOT_MIME_BODY {$placementLabel} {$token}\n");
            $rootResults[]=runRootProbe('AddType with unique extension',
                'Tests mod_mime/FileInfo at this placement. The random extension exists only in the disposable probe tree.',
                $placementLabel,$htaPath,'AddType application/x-hta-root-probe ' . $ext,
                rootUrl($baseUrl,$probeName,$mimeFile,$token),$rootHttp,$logs,$rootTx,
                static function(array $resp): array {
                    $ct=strtolower((string)headerValue($resp,'content-type'));
                    if($resp['status']===200 && str_contains($ct,'application/x-hta-root-probe')) return [true,'Unique MIME mapping was applied.','root-addtype-supported'];
                    if($resp['status']===500) return [false,'AddType caused HTTP 500 at this placement.','root-addtype-rejected'];
                    return [false,'Unique MIME mapping was not observed.','root-addtype-not-effective'];
                },$bodyPreviewBytes);

            $rewriteSource="root-tests/root_rewrite_source_{$slug}_{$token}.txt"; $rewriteTarget="root-tests/root_rewrite_target_{$slug}_{$token}.txt";
            $runner->write($rewriteSource,"ROOT_REWRITE_SOURCE {$placementLabel} {$token}\n"); $runner->write($rewriteTarget,"ROOT_REWRITE_TARGET {$placementLabel} {$token}\n");
            $sourcePath=parse_url(rootUrl($baseUrl,$probeName,$rewriteSource,$token),PHP_URL_PATH) ?: ('/'.$probeName.'/'.$rewriteSource);
            $targetPath=parse_url(rootUrl($baseUrl,$probeName,$rewriteTarget,$token),PHP_URL_PATH) ?: ('/'.$probeName.'/'.$rewriteTarget);
            $rewrite="RewriteEngine On\nRewriteCond %{REQUEST_URI} ={$sourcePath}\nRewriteRule ^ {$targetPath} [END]";
            $rootResults[]=runRootProbe('mod_rewrite exact-URI internal rewrite',
                'Tests rewrite directives at this placement while RewriteCond restricts the rule to one unique URI.',
                $placementLabel,$htaPath,$rewrite,rootUrl($baseUrl,$probeName,$rewriteSource,$token),$rootHttp,$logs,$rootTx,
                static function(array $resp) use($placementLabel,$token): array {
                    if($resp['status']===200 && str_contains($resp['body'],"ROOT_REWRITE_TARGET {$placementLabel} {$token}")) return [true,'Exact-URI root rewrite reached the target file.','root-rewrite-supported'];
                    if($resp['status']===500) return [false,'Root rewrite block caused HTTP 500.','root-rewrite-rejected'];
                    return [false,'Root rewrite did not return the expected target marker.','root-rewrite-not-effective'];
                },$bodyPreviewBytes);

            if($placementActive) {
                $optFile="root-tests/root_options_{$slug}_{$token}.txt"; $runner->write($optFile,"ROOT_OPTIONS_BODY {$placementLabel} {$token}\n");
                $optMarker="{$slug}-options-{$token}";
                foreach(['Options -Indexes'=>'root-options-minus-indexes','Options -MultiViews'=>'root-options-minus-multiviews'] as $optionLine=>$class) {
                    $directives=$optionLine . "\nHeader always set X-HTA-Root-Option \"{$optMarker}\"";
                    $rootResults[]=runRootProbe($optionLine,
                        'Tests whether this specific Options token is accepted at the root placement; the Header marker proves this placement was active.',
                        $placementLabel,$htaPath,$directives,rootUrl($baseUrl,$probeName,$optFile,$token),$rootHttp,$logs,$rootTx,
                        static function(array $resp) use($optMarker,$class): array {
                            $m=headerValue($resp,'x-hta-root-option');
                            if($resp['status']===200 && $m===$optMarker) return [true,'Options token was accepted and placement marker remained active.',$class.'-supported'];
                            if($resp['status']===500) return [false,'Options token caused HTTP 500 at this placement.',$class.'-rejected'];
                            return [false,'Could not prove both Options acceptance and active placement.',$class.'-inconclusive'];
                        },$bodyPreviewBytes);
                }

                $idxDir="root-tests/root_index_dir_{$slug}_{$token}"; $idxName="root_index_{$slug}_{$token}.txt";
                $runner->write($idxDir.'/'.$idxName,"ROOT_DIRECTORYINDEX {$placementLabel} {$token}\n"); $idxMarker="{$slug}-dirindex-{$token}";
                $idx="DirectoryIndex {$idxName} index.php index.html index.htm index.shtml\nHeader always set X-HTA-Root-Option \"{$idxMarker}\"";
                $rootResults[]=runRootProbe('DirectoryIndex (Indexes override class)',
                    'Tests the distinct Indexes override class. A random first index candidate exists only inside the probe directory; common defaults follow it.',
                    $placementLabel,$htaPath,$idx,rootUrl($baseUrl,$probeName,$idxDir.'/',$token),$rootHttp,$logs,$rootTx,
                    static function(array $resp) use($idxMarker,$placementLabel,$token): array {
                        $m=headerValue($resp,'x-hta-root-option');
                        if($resp['status']===200 && $m===$idxMarker && str_contains($resp['body'],"ROOT_DIRECTORYINDEX {$placementLabel} {$token}")) return [true,'Random root-level DirectoryIndex candidate was selected.','root-directoryindex-supported'];
                        if($resp['status']===500) return [false,'DirectoryIndex caused HTTP 500 at this placement.','root-directoryindex-rejected'];
                        return [false,'Could not prove root-level DirectoryIndex behavior at this placement.','root-directoryindex-inconclusive'];
                    },$bodyPreviewBytes);
            } else {
                out('[SKIP] Options and DirectoryIndex probes at ' . $placementLabel . ': placement marker not proven active.');
            }
          } catch (RuntimeException $e) {
            // A failed transaction restores itself before throwing; only a restore problem is fatal.
            if ($rootTx->hadRestoreProblem()) throw $e;
            out('[SKIP] remaining root tests at ' . $placementLabel . ': ' . $e->getMessage());
          }
        }
    }

    // Summary / diagnostic analysis.
    $results = array_merge($runner->results(), $rootResults);

    // Add explicit metadata for manual probes whose .htaccess was prepared separately.
    foreach ($results as &$rr) {
        if (($rr['name'] ?? '') === '<Files> container: public file' || ($rr['name'] ?? '') === '<Files> container: protected file') {
            $rr['placement']='NestedProbe'; $rr['htaccess_path']=$probeRoot . '/files-container/.htaccess';
            $rr['directives']="<Files \"secret.txt\"> Require all denied </Files>";
        } elseif (($rr['name'] ?? '') === 'Placement: parent .htaccess applies to same directory') {
            $rr['placement']='NestedPlacementParent'; $rr['htaccess_path']=$probeRoot . '/scope/.htaccess';
            $rr['directives']='Header set X-HTA-Parent <token>; Header set X-HTA-Scope parent';
        } elseif (($rr['name'] ?? '') === 'Placement: nested inheritance + child override') {
            $rr['placement']='NestedPlacementChild'; $rr['htaccess_path']=$probeRoot . '/scope/.htaccess + ' . $probeRoot . '/scope/child/.htaccess';
            $rr['directives']='parent Header markers + child Header markers/override';
        }
    }
    unset($rr);

    $counts=['SUPPORTED'=>0,'ACTIVE'=>0,'OK'=>0,'REJECTED'=>0,'ACCEPTED/NO-EFFECT'=>0,'INCONCLUSIVE'=>0];
    foreach($results as $r) { $st=resultState($r); if(!isset($counts[$st])) $counts[$st]=0; $counts[$st]++; }

    vout();
    vout(str_repeat('=', 78));
    vout('DIAGNOSTIC SUMMARY');
    vout(str_repeat('=', 78));
    vout('This summary answers three separate questions:');
    vout('  1. Which .htaccess placements are actually read?');
    vout('  2. Which directive families/settings work or are rejected at the tested placement?');
    vout('  3. What common Apache/shared-hosting configuration pattern best fits the observations?');
    vout();

    printAtAGlance($results, $testDocrootHtaccess, $testDomainHtaccess);
    printPlacementAnalysis($results, $domainDir, $docroot, $probeRoot, $testDocrootHtaccess, $testDomainHtaccess);
    printCapabilityAnalysis($results);
    printConfigurationHints($results, $testDocrootHtaccess, $testDomainHtaccess);
    printDetailedResultLedger($results);

    vout(str_repeat('-', 78));
    vout('State counts: ' . implode('; ', array_map(static fn($k,$v)=>$k . '=' . $v, array_keys($counts), array_values($counts))) . '; total=' . count($results));
    vout();
    vout('State meanings:');
    vout('  SUPPORTED          requested behavior was directly demonstrated.');
    vout('  ACTIVE             placement/parsing behavior was directly demonstrated.');
    vout('  OK                 control/environment probe succeeded.');
    vout('  REJECTED           valid setting produced a server/configuration rejection at that placement.');
    vout('  ACCEPTED/NO-EFFECT Apache accepted the setting, but the downstream behavior did not change.');
    vout('  INCONCLUSIVE       result does not cleanly distinguish support from another cause.');
    vout();
    vout('Important interpretation rule: an HTTP 500 from a valid directive proves rejection at that');
    vout('placement, but the error log is needed to distinguish e.g. "directive not allowed here" from');
    vout('"unknown directive/module not loaded". The report therefore says REJECTED, not simply unsupported.');
    if ($testDocrootHtaccess || $testDomainHtaccess) {
        vout('Root tests ran (default; --no-root / root=0 disables them). Each live root block was backed up, appended');
        vout('for one request, then removed. A SIGKILL/power loss cannot execute PHP cleanup; retain any printed backup path if interrupted.');
    } else {
        vout('Root tests were not enabled. They were disabled or not possible here (see the SKIP lines).');
    }
    vout();
    printCompactSummary($results, $testDocrootHtaccess, $testDomainHtaccess);
    if ($rootTx !== null) {
        if ($rootTx->hadRestoreProblem()) {
            out(); out('IMPORTANT: at least one root restoration reported a problem. Backups were intentionally kept at:');
            out('  ' . $rootTx->backupDir());
        } else {
            $rootTx->cleanupBackupsIfSafe();
            vout(); vout('Root .htaccess transactions restored successfully; temporary root backups were removed.');
        }
    }
    if ($keep) {
        out('Probe files were kept for debugging:');
        out('  FS : ' . $probeRoot);
        out('  URL: ' . rtrim($baseUrl, '/') . '/' . $probeName . '/');
        out("Delete them when finished: rm -rf '" . str_replace("'", "'\\''", $probeRoot) . "'");
    } else {
        $cleanup();
        vout('Probe directory cleaned up.');
    }
    if ($jsonTarget !== null) writeJsonReport($jsonTarget, $results, [
        'version' => SCRIPT_VERSION, 'mode' => $isWeb ? 'web' : 'cli', 'php' => PHP_VERSION, 'sapi' => PHP_SAPI,
        'base_url' => $baseUrl, 'root_tests' => ['docroot' => $testDocrootHtaccess, 'above_docroot' => $testDomainHtaccess],
        'root_restore_problem' => $rootTx !== null && $rootTx->hadRestoreProblem(), 'notes' => array_merge($rootNotes, $notes),
        'web_runtime' => ['php' => extractRuntimeValue($results, 'WEB_PHP_VERSION'), 'sapi' => extractRuntimeValue($results, 'WEB_PHP_SAPI'), 'server' => extractRuntimeValue($results, 'SERVER_SOFTWARE')],
    ]);
    if ($isWeb && $jsonTarget === null) out("\nDELETE THIS FILE from the server now (it can modify .htaccess files).");
    exit($rootTx !== null && $rootTx->hadRestoreProblem() ? 3 : 0);
} catch (Throwable $e) {
    $GLOBALS['HTA_FATAL'] = $e->getMessage();
    err('FATAL: ' . $e->getMessage());
    if ($GLOBALS['HTA_VERBOSE'] || !($e instanceof RuntimeException)) err('At ' . $e->getFile() . ':' . $e->getLine());
    if (isset($rootTx) && $rootTx instanceof RootHtaccessTransaction && $rootTx->hadRestoreProblem()) {
        err('IMPORTANT: a root .htaccess restoration reported a problem; backups kept at ' . $rootTx->backupDir());
        exit(3);
    }
    exit(1);
}

