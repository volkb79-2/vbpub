<?php
/**
 * test-webspace-php-environment.php - generic PHP environment report for shared webspace.
 *
 * PURPOSE
 *   Answers "what can I rely on here?" before deploying a PHP application: PHP version and
 *   limits, disabled functions, open_basedir, extensions, SQLite features, writable places
 *   (temp, session, sibling directories of the web root), mail, outbound HTTPS, DNS, proxy/HTTPS
 *   detection, .user.ini support, CLI php binaries and cron hints, filesystem behaviour.
 *   For .htaccess behaviour use the sibling script test-webspace-htaccess-support.php.
 *
 * USAGE
 *   1. Set $TOKEN below to a long random string, e.g. from `openssl rand -hex 16`.
 *   2. Upload this single file into the web root of the hoster.
 *   3. Open  https://DOMAIN/test-webspace-php-environment.php?token=YOUR-TOKEN
 *      Optional parameters:
 *        &format=json      same data, structured (save it and send it back)
 *        &verbose=1        add raw dumps (ini_get_all, full extension list)
 *        &sibling=NAME     also check <parent of web root>/NAME (exists / writable / open_basedir)
 *   4. DELETE THE FILE AGAIN. It reveals server paths and configuration.
 *
 * WHAT IT TOUCHES
 *   Reads settings only, except: small temporary files (names start with "phpenvtest-") in the
 *   system temp dir, in the web root, in the session save path and in &sibling=NAME (only if
 *   writable; a short-lived SQLite test database goes into the sibling dir, else the web root,
 *   else temp), plus a DNS lookup and up to two outbound HTTPS requests (streams, cURL) to
 *   www.google.com. Every file it creates it deletes again. No environment dump; values that
 *   look like passwords (e.g. auth= in a Redis session.save_path) are masked.
 *   Runs on PHP 7.2+ (no PHP 8-only syntax) so it can report even on an old default PHP.
 */

$TOKEN = '';   // <- REQUIRED: put a long random string here, e.g. from `openssl rand -hex 16`

// ---------------------------------------------------------------------------------------
if (strlen($TOKEN) < 16 || !isset($_GET['token']) || !is_string($_GET['token']) || !hash_equals($TOKEN, $_GET['token'])) {
    http_response_code(403);
    header('Content-Type: text/plain; charset=utf-8');
    echo "Forbidden. Set \$TOKEN in this file (at least 16 characters) and call ?token=...\n";
    exit;
}
header('Cache-Control: no-store');
header('X-Robots-Tag: noindex');
// Values that the next lines change, as configured by the hoster.
$origMaxExec = (string) ini_get('max_execution_time');
$origDisplayErrors = (string) ini_get('display_errors');
// Since PHP 8 a function listed in disable_functions is undefined: calling it throws, even with @.
if (function_exists('set_time_limit')) { @set_time_limit(60); }
// display_errors=1 would print warnings into the report and break the JSON: collect them instead.
if (function_exists('ini_set')) { @ini_set('display_errors', '0'); }
$phpWarnings = array();
set_error_handler(function ($no, $msg, $file, $line) use (&$phpWarnings) {
    if (!(error_reporting() & $no)) { return true; }   // honour the @ operator
    $phpWarnings[] = $msg . ' (line ' . $line . ')';
    return true;
});

$verbose = !empty($_GET['verbose']);
$checks = array();      // list of [section, status, label, detail, hint]
$raw = array();         // verbose-only dumps
$created = array();     // temp files we made, removed at the end no matter what

function chk($section, $status, $label, $detail = '', $hint = '')
{
    global $checks;
    $checks[] = array('section' => $section, 'status' => $status, 'label' => $label, 'detail' => (string) $detail, 'hint' => $hint);
}
function yn($b) { return $b ? 'yes' : 'no'; }
function disabledList()
{
    return array_filter(array_map('trim', explode(',', (string) ini_get('disable_functions'))));
}
function fnUsable($name)
{
    return function_exists($name) && !in_array($name, disabledList(), true);
}
/** Mask values that look like credentials (redis/memcached "auth=", "user:pass@", password=...). */
function redact($s)
{
    $s = preg_replace('~(auth|pass(word)?|pwd|secret|token)=([^&;\s]+)~i', '$1=***', (string) $s);
    return preg_replace('~(//[^/:@\s]+):[^@/\s]+@~', '$1:***@', $s);
}
function toBytes($v)
{
    $v = trim((string) $v);
    if ($v === '' || $v === '-1') { return -1; }
    $n = (float) $v;
    switch (strtolower(substr($v, -1))) {
        case 'g': $n *= 1024;
        case 'm': $n *= 1024;
        case 'k': $n *= 1024;
    }
    return (int) $n;
}
function obdEntries()
{
    $s = trim((string) ini_get('open_basedir'));
    if ($s === '') { return array(); }
    $out = array();
    foreach (explode(PATH_SEPARATOR, $s) as $e) { $e = trim($e); if ($e !== '') { $out[] = $e; } }
    return $out;
}
// open_basedir turns is_dir()/is_writable() on forbidden paths into a silent "no": test explicitly.
function obdAllows($path)
{
    $entries = obdEntries();
    if (!$entries) { return true; }
    $paths = array(rtrim($path, '/'));
    $real = @realpath($path);   // PHP itself compares the resolved path (symlinked docroots)
    if ($real !== false) { $paths[] = rtrim($real, '/'); }
    foreach ($entries as $entry) {
        $cands = array(rtrim($entry, '/'));
        $er = @realpath($entry);
        if ($er !== false) { $cands[] = rtrim($er, '/'); }
        foreach ($cands as $e) {
            foreach ($paths as $p) {
                if ($e !== '' && ($p === $e || strpos($p . '/', $e . '/') === 0)) { return true; }
            }
        }
    }
    return false;
}
function mkTemp($dir, $suffix = '')
{
    global $created;
    $f = rtrim($dir, '/') . '/phpenvtest-' . bin2hex(random_bytes(4)) . $suffix;
    $created[] = $f;
    return $f;
}
function writeProbe($dir)
{
    if (!obdAllows($dir) || !is_dir($dir)) { return false; }
    $f = mkTemp($dir, '.tmp');
    $ok = @file_put_contents($f, 'x') === 1;
    if (is_file($f)) { @unlink($f); }
    return $ok;
}

$docroot = (string) (isset($_SERVER['DOCUMENT_ROOT']) && $_SERVER['DOCUMENT_ROOT'] !== '' ? $_SERVER['DOCUMENT_ROOT'] : __DIR__);
$parent = dirname($docroot);
$tmp = sys_get_temp_dir();

// --- PHP ---------------------------------------------------------------------------------
$S = 'PHP';
// 8.1 is end of life since 2025-12-31 (php.net/supported-versions).
chk($S, version_compare(PHP_VERSION, '8.2', '<') ? 'WARN' : 'OK', 'version', PHP_VERSION, 'PHP without security support: pick a newer PHP version for this domain in the hoster panel.');
chk($S, 'INFO', 'SAPI', PHP_SAPI . (isset($_SERVER['SERVER_SOFTWARE']) ? '  | server: ' . $_SERVER['SERVER_SOFTWARE'] : ''));
chk($S, 'INFO', 'OS', PHP_OS . (fnUsable('php_uname') ? ' ' . php_uname('r') . ' ' . php_uname('m') : ' (php_uname disabled)'));
$uid = function_exists('posix_geteuid') ? posix_geteuid() : null;
$uname = ($uid !== null && function_exists('posix_getpwuid')) ? (isset(posix_getpwuid($uid)['name']) ? posix_getpwuid($uid)['name'] : '?') : (fnUsable('get_current_user') ? get_current_user() : '?');
chk($S, 'INFO', 'user / uid', $uname . ($uid !== null ? ' / ' . $uid : ' / posix unavailable'));
$iniScanned = fnUsable('php_ini_scanned_files') ? (string) php_ini_scanned_files() : '';
chk($S, 'INFO', 'php.ini', (fnUsable('php_ini_loaded_file') ? ((string) php_ini_loaded_file() !== '' ? php_ini_loaded_file() : '(none)') : '?') . ($iniScanned !== '' ? '  (+ scanned dir files)' : ''));
chk($S, 'INFO', 'PHP_BINARY', PHP_BINARY);
chk($S, 'INFO', 'document root', $docroot);
chk($S, 'INFO', 'this file', __FILE__);
$tz = (string) ini_get('date.timezone');
chk($S, $tz === '' ? 'WARN' : 'OK', 'timezone', 'date.timezone=' . ($tz === '' ? '(empty)' : $tz) . '  effective=' . date_default_timezone_get(), 'Set date.timezone (php.ini/.user.ini) or call date_default_timezone_set() in the app.');
$loc = function_exists('setlocale') ? setlocale(LC_ALL, 0) : '?';
chk($S, 'INFO', 'locale', (string) $loc . '  | default_charset=' . ini_get('default_charset'));
if (function_exists('setlocale')) {
    $de = @setlocale(LC_ALL, 'de_DE.UTF-8', 'de_DE.utf8', 'de_DE');
    @setlocale(LC_ALL, $loc);
    chk($S, 'INFO', 'locale de_DE available', yn($de !== false));
}

// --- limits ------------------------------------------------------------------------------
$S = 'LIMITS';
$mem = toBytes(ini_get('memory_limit'));
chk($S, ($mem !== -1 && $mem < 128 * 1048576) ? 'WARN' : 'OK', 'memory_limit', ini_get('memory_limit'), 'Raise memory_limit to >=128M if the application needs it.');
$met = (int) $origMaxExec;   // read before set_time_limit(60) above changed it
chk($S, ($met !== 0 && $met < 30) ? 'WARN' : 'OK', 'max_execution_time', $met . 's' . (PHP_SAPI === 'cli' ? ' (CLI is always 0)' : ''), 'Long imports/exports may be cut off.');
$up = toBytes(ini_get('upload_max_filesize'));
$post = toBytes(ini_get('post_max_size'));
chk($S, ($up !== -1 && $up < 8 * 1048576) ? 'WARN' : 'OK', 'upload_max_filesize', ini_get('upload_max_filesize'), 'Small upload limit; raise it if users upload files.');
chk($S, ($post > 0 && $up > 0 && $post < $up) ? 'WARN' : 'OK', 'post_max_size', ini_get('post_max_size'), 'post_max_size should be >= upload_max_filesize.');
chk($S, 'INFO', 'file_uploads', yn(ini_get('file_uploads')));
$miv = (int) ini_get('max_input_vars');
chk($S, $miv < 1000 ? 'WARN' : 'OK', 'max_input_vars', $miv, 'Large forms get truncated silently below ~1000.');
chk($S, 'INFO', 'allow_url_fopen', yn(ini_get('allow_url_fopen')));
chk($S, 'INFO', 'display_errors / log_errors', ($origDisplayErrors !== '' && $origDisplayErrors !== '0' ? $origDisplayErrors : 'off') . ' / ' . (ini_get('log_errors') ?: 'off') . '  error_log=' . (ini_get('error_log') ?: '(none)'));

// --- disabled functions ------------------------------------------------------------------
$S = 'DISABLED FUNCTIONS';
$dis = disabledList();
chk($S, 'INFO', 'disable_functions', $dis ? count($dis) . ' entries' . ($verbose ? ': ' . implode(', ', $dis) : ' (add &verbose=1 for the list)') : '(none)');
$matter = array(
    'exec' => 'No CLI discovery, no shelling out.',
    'proc_open' => 'Libraries that spawn processes (mail transports, image tools) will fail.',
    'shell_exec' => 'No shelling out.',
    'mail' => 'PHP mail() unavailable: use SMTP via a library.',
    'curl_exec' => 'cURL requests blocked: HTTP clients must fall back to streams, if allowed.',
    'fsockopen' => 'Raw sockets (SMTP, some HTTP clients) blocked.',
);
foreach ($matter as $fn => $hint) {
    $off = in_array($fn, $dis, true) || !function_exists($fn);
    chk($S, $off ? 'WARN' : 'OK', $fn, $off ? 'unavailable' : 'available', $hint);
}

// --- open_basedir / filesystem -----------------------------------------------------------
$S = 'OPEN_BASEDIR / PATHS';
$obd = obdEntries();
if (!$obd) {
    chk($S, 'INFO', 'open_basedir', '(none - PHP may access the whole account)');
} else {
    chk($S, 'INFO', 'open_basedir', count($obd) . ' entries: ' . implode('  ', $obd), '');
    $wOut = array();
    foreach ($obd as $e) {
        if (is_dir($e) && is_writable($e)) {
            $inside = strpos(rtrim($e, '/') . '/', rtrim($docroot, '/') . '/') === 0;
            $wOut[] = $e . ($inside ? ' (inside docroot)' : ' (OUTSIDE docroot)');
        }
    }
    chk($S, $wOut ? 'OK' : 'WARN', 'writable open_basedir entries', $wOut ? implode('  ', $wOut) : '(none)', 'Without a writable place outside the web root, data files must live inside the web root.');
}
$parentBlocked = !obdAllows($parent);
if ($parentBlocked) {
    chk($S, 'WARN', 'parent of docroot', $parent . '  BLOCKED by open_basedir (PHP cannot even test existence)', 'Extend open_basedir in the hoster panel if files outside the web root are needed.');
} else {
    $pw = is_writable($parent);
    chk($S, is_dir($parent) ? 'OK' : 'WARN', 'parent of docroot reachable', $parent . '  exists=' . yn(is_dir($parent)) . ' readable=' . yn(is_readable($parent)));
    chk($S, $pw ? 'OK' : 'INFO', 'parent of docroot writable', yn($pw), 'Not writable is common; sibling directories created by hand may still be writable.');
}
$sib = isset($_GET['sibling']) && is_string($_GET['sibling']) ? $_GET['sibling'] : '';
$sibWritable = '';   // set to the sibling path when it is writable (preferred place for the SQLite probe)
if ($sib !== '') {
    if (!preg_match('/^[A-Za-z0-9._-]{1,64}$/', $sib) || $sib === '.' || $sib === '..') {
        chk($S, 'FAIL', 'sibling=' . substr($sib, 0, 40), 'invalid name (allowed: letters, digits, . _ -)', 'Pass a plain directory name.');
    } else {
        $sd = $parent . '/' . $sib;
        if (!obdAllows($sd)) {
            chk($S, 'FAIL', 'sibling ' . $sib, $sd . '  BLOCKED by open_basedir', 'Extend open_basedir to include the parent directory.');
        } elseif (!is_dir($sd)) {
            chk($S, 'WARN', 'sibling ' . $sib, $sd . '  does not exist', 'Create the directory (FTP/panel) and re-run.');
        } else {
            $ok = writeProbe($sd);
            if ($ok) { $sibWritable = $sd; }
            chk($S, $ok ? 'OK' : 'FAIL', 'sibling ' . $sib, $sd . '  exists=yes writable=' . yn($ok), 'Directory exists but PHP cannot write: fix permissions/owner.');
        }
    }
} else {
    chk($S, 'INFO', 'sibling check', 'add &sibling=NAME to test <parent>/NAME');
}
$dw = writeProbe($docroot);
chk($S, 'INFO', 'docroot writable (create+delete file)', yn($dw), 'Writable web root is convenient for deploy but means a compromised script can plant files.');

// --- temp, session, disk -----------------------------------------------------------------
$S = 'TEMP / SESSION / DISK';
$tw = writeProbe($tmp);
chk($S, $tw ? 'OK' : 'FAIL', 'temp dir writable', $tmp . '  ' . yn($tw), 'Set sys_temp_dir/upload_tmp_dir to a writable path.');
$free = (obdAllows($tmp) && fnUsable('disk_free_space')) ? @disk_free_space($tmp) : false;
chk($S, $free === false ? 'INFO' : ($free < 50 * 1048576 ? 'WARN' : 'OK'), 'free disk space (temp)', $free === false ? 'unknown' : round($free / 1048576) . ' MB', 'Low free space.');
$freeDoc = (obdAllows($docroot) && fnUsable('disk_free_space')) ? @disk_free_space($docroot) : false;
chk($S, 'INFO', 'free disk space (docroot)', $freeDoc === false ? 'unknown' : round($freeDoc / 1048576) . ' MB');
chk($S, 'INFO', 'upload_tmp_dir', ini_get('upload_tmp_dir') ?: '(default = temp dir)');
$ssp = (string) ini_get('session.save_path');
$sspPath = $ssp;
if (($p = strrpos($ssp, ';')) !== false) { $sspPath = substr($ssp, $p + 1); }   // "N;/path" form
$sh = (string) ini_get('session.save_handler');
if ($sh !== 'files') {
    chk($S, 'INFO', 'session handler', $sh . '  save_path=' . redact($ssp));
} else {
    $sdir = $sspPath !== '' ? $sspPath : $tmp;
    $sok = writeProbe($sdir);
    chk($S, $sok ? 'OK' : 'FAIL', 'session save path writable', $sdir . '  ' . yn($sok), 'Sessions will not persist: set session.save_path to a writable directory.');
}
$isHttps = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off');
chk($S, 'INFO', 'session cookie flags', 'secure=' . (ini_get('session.cookie_secure') ?: '0') . ' httponly=' . (ini_get('session.cookie_httponly') ?: '0') . ' samesite=' . (ini_get('session.cookie_samesite') ?: '(unset)') . ' strict_mode=' . (ini_get('session.use_strict_mode') ?: '0'));

// --- filesystem behaviour ----------------------------------------------------------------
$S = 'FILESYSTEM';
if ($tw) {
    if (fnUsable('umask')) { chk($S, 'INFO', 'umask', sprintf('%04o', umask())); }
    $f = mkTemp($tmp, '.mode');
    if (@file_put_contents($f, 'x') !== false) {
        clearstatcache();
        chk($S, 'INFO', 'new file mode', substr(sprintf('%o', fileperms($f)), -4));
        @unlink($f);
    }
    $d = mkTemp($tmp, '.dir');
    if (@mkdir($d, 0777)) {
        clearstatcache();
        chk($S, 'INFO', 'new directory mode (mkdir 0777)', substr(sprintf('%o', fileperms($d)), -4));
        @rmdir($d);
    }
    // case sensitivity
    $base = mkTemp($tmp, '.case');
    $lower = $base . 'a'; $upper = $base . 'A';
    $created[] = $lower; $created[] = $upper;
    if (@file_put_contents($lower, 'x') !== false) {
        $ci = is_file($upper);
        chk($S, 'INFO', 'filesystem case sensitivity', $ci ? 'case-INSENSITIVE' : 'case-sensitive', $ci ? 'File name case mistakes will not be noticed here but break on Linux.' : '');
        @unlink($lower);
    }
    // max file name length
    $maxLen = 0;
    foreach (array(255, 200, 143, 100) as $len) {
        $name = 'phpenvtest-' . substr(str_repeat('n', $len), 0, $len - 11);
        $path = $tmp . '/' . $name;
        $created[] = $path;
        if (@file_put_contents($path, 'x') !== false) { @unlink($path); $maxLen = $len; break; }
    }
    chk($S, $maxLen >= 255 ? 'OK' : 'WARN', 'max file name length (tested)', $maxLen ? '>= ' . $maxLen . ' bytes' : 'could not create long names', 'Short file name limit (e.g. encrypted home dir).');
} else {
    chk($S, 'INFO', 'filesystem behaviour', 'skipped: temp dir not writable');
}
$mounts = (obdAllows('/proc/mounts')) ? @file('/proc/mounts', FILE_IGNORE_NEW_LINES) : false;
if ($mounts) {
    $best = function ($path) use ($mounts) {
        $path = @realpath($path) ?: $path; $hit = null; $len = -1;
        foreach ($mounts as $line) {
            $p = explode(' ', $line);
            if (count($p) < 3) { continue; }
            $mp = str_replace('\040', ' ', $p[1]);
            if (($path === $mp || strpos($path, rtrim($mp, '/') . '/') === 0) && strlen($mp) > $len) { $hit = $p[2]; $len = strlen($mp); }
        }
        return $hit ?: 'unknown';
    };
    foreach (array('docroot' => $docroot, 'temp' => $tmp) as $label => $dir) {
        $t = $best($dir);
        $net = in_array($t, array('nfs', 'nfs4', 'cifs', 'smbfs', 'fuse', 'fuseblk', 'ceph'), true);
        chk($S, $net ? 'WARN' : 'INFO', 'filesystem type ' . $label, $t, 'Network/fuse filesystem: file locking and SQLite WAL are unreliable.');
    }
} else {
    chk($S, 'INFO', 'filesystem type', '/proc/mounts not readable (open_basedir or non-Linux)');
}

// --- extensions --------------------------------------------------------------------------
$S = 'EXTENSIONS';
$exts = array('curl', 'mbstring', 'intl', 'gd', 'imagick', 'zip', 'xml', 'dom', 'simplexml', 'openssl', 'sodium', 'iconv', 'ctype', 'fileinfo', 'bcmath', 'gmp', 'opcache', 'apcu', 'redis', 'memcached', 'pdo_mysql', 'mysqli', 'pdo_pgsql', 'pdo_sqlite', 'sqlite3');
$core = array('openssl', 'mbstring', 'ctype', 'fileinfo', 'curl', 'xml', 'dom', 'simplexml', 'iconv', 'zip');
$have = array(); $miss = array();
foreach ($exts as $e) {
    if (extension_loaded($e) || ($e === 'opcache' && extension_loaded('Zend OPcache'))) { $have[] = $e; } else { $miss[] = $e; }
}
chk($S, 'INFO', 'loaded', implode(', ', $have));
$missCore = array_values(array_intersect($miss, $core));
chk($S, $missCore ? 'WARN' : 'OK', 'common extensions missing', $miss ? implode(', ', $miss) : '(none)', $missCore ? 'Enable ' . implode(', ', $missCore) . ' in the hoster PHP settings if the application needs them.' : '');
$oc = function_exists('opcache_get_status') ? @opcache_get_status(false) : false;
if (is_array($oc)) {
    chk($S, !empty($oc['opcache_enabled']) ? 'OK' : 'WARN', 'opcache status', 'enabled=' . yn(!empty($oc['opcache_enabled'])) . ' memory_used=' . (isset($oc['memory_usage']['used_memory']) ? round($oc['memory_usage']['used_memory'] / 1048576, 1) . ' MB' : '?') . ' validate_timestamps=' . ini_get('opcache.validate_timestamps') . ' revalidate_freq=' . ini_get('opcache.revalidate_freq'), 'Opcache off: slower; with validate_timestamps=0 code changes need a reset.');
} else {
    chk($S, 'INFO', 'opcache status', function_exists('opcache_get_status') ? 'not available (restricted)' : 'extension not loaded');
}
if ($verbose) { $raw['extensions_all_loaded'] = get_loaded_extensions(); }

// --- SQLite ------------------------------------------------------------------------------
$S = 'SQLITE';
$drivers = class_exists('PDO') ? PDO::getAvailableDrivers() : array();
chk($S, 'INFO', 'PDO drivers', implode(', ', $drivers));
chk($S, class_exists('SQLite3') ? 'OK' : 'INFO', 'SQLite3 class', class_exists('SQLite3') ? SQLite3::version()['versionString'] : 'missing');
if (!in_array('sqlite', $drivers, true)) {
    chk($S, 'WARN', 'pdo_sqlite', 'missing', 'Enable pdo_sqlite or use another database.');
} else {
    try {
        $m = new PDO('sqlite::memory:', null, null, array(PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION));
        $ver = (string) $m->query('select sqlite_version()')->fetchColumn();
        chk($S, version_compare($ver, '3.24', '<') ? 'WARN' : 'OK', 'pdo_sqlite version', $ver, 'Old SQLite: UPSERT/window functions may be missing.');
        $opts = $m->query('pragma compile_options')->fetchAll(PDO::FETCH_COLUMN);
        if ($verbose) { $raw['sqlite_compile_options'] = $opts; }
        $try = function ($label, $sql, $hint) use ($m, $S) {
            try { $m->exec($sql); chk($S, 'OK', $label, 'yes'); } catch (Exception $e) { chk($S, 'WARN', $label, 'NO: ' . $e->getMessage(), $hint); } catch (Throwable $e) { chk($S, 'WARN', $label, 'NO: ' . $e->getMessage(), $hint); }
        };
        $try('FTS5', "create virtual table t_fts using fts5(a, tokenize='unicode61')", 'No full-text search via FTS5.');
        $try('JSON1', "select json_extract('{\"a\":1}', '$.a')", 'No json_* SQL functions.');
        $try('window functions', 'select row_number() over (order by 1) from (select 1)', 'Needs SQLite >= 3.25.');
        $try('UPSERT', 'create table u(k primary key, v); insert into u values(1,1) on conflict(k) do update set v=2', 'Needs SQLite >= 3.24.');
        try { $m->exec('create table r(x)'); $m->query('insert into r values(1) returning x'); chk($S, 'INFO', 'RETURNING (3.35)', 'yes'); } catch (Exception $e) { chk($S, 'INFO', 'RETURNING (3.35)', 'no'); } catch (Throwable $e) { chk($S, 'INFO', 'RETURNING (3.35)', 'no'); }
        chk($S, 'INFO', 'lower("Ä")', (string) $m->query("select lower('Ä')")->fetchColumn() . ' (ASCII-only unless ICU compiled in)');
    } catch (Exception $e) {
        chk($S, 'WARN', 'pdo_sqlite in-memory', $e->getMessage());
    } catch (Throwable $e) {
        chk($S, 'WARN', 'pdo_sqlite in-memory', $e->getMessage());
    }
    // WAL / locking probe where an application database would live: the sibling data dir if
    // given, else the web root (same filesystem), else temp.
    $pd = $sibWritable !== '' ? $sibWritable : ($dw ? $docroot : ($tw ? $tmp : ''));
    $pdLabel = $pd === $sibWritable ? 'sibling dir' : ($pd === $docroot ? 'docroot' : 'temp dir');
    if ($pd === '') {
        chk($S, 'INFO', 'WAL probe', 'skipped: no writable directory');
    } else {
        $file = mkTemp($pd, '.sqlite');
        $created[] = $file . '-wal'; $created[] = $file . '-shm'; $created[] = $file . '-journal';
        try {
            $db = new PDO('sqlite:' . $file, null, null, array(PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION));
            $db->exec('create table t(a)');
            $mode = strtolower((string) $db->query('pragma journal_mode=wal')->fetchColumn());
            if ($mode === 'wal') {
                $db->exec('insert into t values(1)');
                $second = new PDO('sqlite:' . $file, null, null, array(PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION));
                $second->beginTransaction(); $second->query('select * from t')->fetchAll();
                $detail = 'WAL granted';
                try { $db->exec('insert into t values(2)'); $detail .= '; write while another connection reads: ok'; chk($S, 'OK', 'WAL probe (' . $pdLabel . ')', $detail); }
                catch (Exception $e) { chk($S, 'WARN', 'WAL probe (' . $pdLabel . ')', $detail . '; concurrent write failed: ' . $e->getMessage(), 'Use journal_mode=DELETE.'); }
                $second->commit(); $second = null;
            } else {
                chk($S, 'WARN', 'WAL probe (' . $pdLabel . ')', 'requested WAL, got: ' . $mode, 'Use journal_mode=DELETE on this filesystem.');
            }
            $db = null;
            $fl = fopen($file, 'rb');
            $lk = $fl && flock($fl, LOCK_EX | LOCK_NB);
            if ($fl) { flock($fl, LOCK_UN); fclose($fl); }
            chk($S, $lk ? 'OK' : 'WARN', 'flock on db file', yn($lk), 'File locking does not work here: SQLite is unsafe with several writers.');
        } catch (Exception $e) {
            chk($S, 'WARN', 'WAL probe', 'ERROR: ' . $e->getMessage());
        } catch (Throwable $e) {
            chk($S, 'WARN', 'WAL probe', 'ERROR: ' . $e->getMessage());
        }
    }
}

// --- request / HTTPS / proxy -------------------------------------------------------------
$S = 'HTTPS / PROXY';
$xfp = isset($_SERVER['HTTP_X_FORWARDED_PROTO']) ? (string) $_SERVER['HTTP_X_FORWARDED_PROTO'] : '';
chk($S, $isHttps ? 'OK' : 'WARN', 'HTTPS seen by PHP', '$_SERVER[HTTPS]=' . (isset($_SERVER['HTTPS']) ? $_SERVER['HTTPS'] : '(unset)') . '  X-Forwarded-Proto=' . ($xfp !== '' ? $xfp : '(none)') . '  port=' . (isset($_SERVER['SERVER_PORT']) ? $_SERVER['SERVER_PORT'] : '?'), 'If the site is served over HTTPS but PHP sees none, the app must trust X-Forwarded-Proto from the proxy.');
$hints = array();
if ($xfp !== '') { $hints[] = 'X-Forwarded-Proto present'; }
if (isset($_SERVER['HTTP_X_FORWARDED_FOR'])) { $hints[] = 'X-Forwarded-For present'; }
if (isset($_SERVER['HTTP_X_REAL_IP'])) { $hints[] = 'X-Real-IP present'; }
if (isset($_SERVER['HTTP_VIA'])) { $hints[] = 'Via present'; }
if (isset($_SERVER['HTTP_CF_CONNECTING_IP'])) { $hints[] = 'Cloudflare header present'; }
if (stripos((string) (isset($_SERVER['SERVER_SOFTWARE']) ? $_SERVER['SERVER_SOFTWARE'] : ''), 'nginx') !== false) { $hints[] = 'SERVER_SOFTWARE says nginx (.htaccess is NOT evaluated unless Apache runs behind it)'; }
if (PHP_SAPI === 'fpm-fcgi' || PHP_SAPI === 'cgi-fcgi') { $hints[] = 'PHP runs as FastCGI/FPM (often behind nginx or Apache proxy_fcgi)'; }
chk($S, 'INFO', 'proxy / front-end hints', $hints ? implode('; ', $hints) : '(none seen)');
chk($S, 'INFO', 'REMOTE_ADDR', isset($_SERVER['REMOTE_ADDR']) ? $_SERVER['REMOTE_ADDR'] : '?');
$authHdr = isset($_SERVER['HTTP_AUTHORIZATION']) || isset($_SERVER['REDIRECT_HTTP_AUTHORIZATION']);
$remoteUser = isset($_SERVER['REMOTE_USER']) ? 'REMOTE_USER' : (isset($_SERVER['REDIRECT_REMOTE_USER']) ? 'REDIRECT_REMOTE_USER' : '');
chk($S, 'INFO', 'Authorization header reaches PHP', ($authHdr || isset($_SERVER['PHP_AUTH_USER'])) ? 'yes' : 'no (normal if the request carried none)');
// Behind Apache Basic Auth: is the logged-in user visible to PHP? (only the variable names are shown)
chk($S, 'INFO', 'Basic Auth user visible to PHP',
    $remoteUser !== '' ? 'yes, via $_SERVER[' . $remoteUser . ']' . (isset($_SERVER['PHP_AUTH_USER']) ? ' and PHP_AUTH_USER' : '') : (isset($_SERVER['PHP_AUTH_USER']) ? 'only PHP_AUTH_USER (REMOTE_USER unset)' : 'no (open this page behind the Basic Auth login to test it)'),
    'An app that trusts REMOTE_USER will not see the user; read PHP_AUTH_USER or ask the hoster.');
if (function_exists('apache_get_modules')) {
    $mods = apache_get_modules();
    $mm = array();
    foreach (array('mod_rewrite', 'mod_headers', 'mod_deflate', 'mod_expires') as $mod) { $mm[] = $mod . '=' . yn(in_array($mod, $mods, true)); }
    chk($S, 'INFO', 'apache modules (mod_php)', implode(' ', $mm));
} else {
    chk($S, 'INFO', 'apache_get_modules', 'not available (FPM/CGI): test .htaccess with test-webspace-htaccess-support.php');
}

// --- .user.ini / ini_set -----------------------------------------------------------------
$S = 'INI MECHANISMS';
$uif = (string) ini_get('user_ini.filename');
$uiApplies = in_array(PHP_SAPI, array('cgi-fcgi', 'fpm-fcgi', 'cgi', 'litespeed'), true);   // mod_php and CLI never read .user.ini
chk($S, ($uif !== '' && $uiApplies) ? 'OK' : 'INFO', '.user.ini mechanism', ($uiApplies ? '' : 'NOT read by SAPI ' . PHP_SAPI . ' (use php_value in .htaccess)  ') . 'user_ini.filename=' . ($uif !== '' ? $uif : '(disabled / not applicable)') . '  cache_ttl=' . (ini_get('user_ini.cache_ttl') !== false ? ini_get('user_ini.cache_ttl') . 's' : '?'), 'Changes in .user.ini take up to cache_ttl seconds to apply; without it use the panel PHP settings.');
chk($S, 'INFO', 'scanned ini files', $iniScanned !== '' ? 'yes' : 'none');
if (!function_exists('ini_set')) { chk($S, 'WARN', 'ini_set', 'disabled', 'Applications cannot change PHP settings at runtime.'); }
else foreach (array('memory_limit' => '192M', 'max_execution_time' => '31', 'display_errors' => '0', 'date.timezone' => 'UTC', 'post_max_size' => '9M') as $k => $v) {
    $old = ini_get($k);
    $r = @ini_set($k, $v);
    $now = ini_get($k);
    $changed = ($r !== false && (string) $now === (string) $v);
    if ($r !== false) { @ini_set($k, $old); }
    chk($S, 'INFO', 'ini_set ' . $k, $changed ? 'works' : 'no effect (PHP_INI_PERDIR or locked)');
}
if ($verbose && fnUsable('ini_get_all')) {
    $raw['ini_get_all'] = array();
    foreach ((array) ini_get_all(null, false) as $k => $v) {
        $raw['ini_get_all'][$k] = preg_match('/(pass|_pw$|secret|token)/i', $k) && (string) $v !== '' ? '***' : redact($v);
    }
}

// --- mail --------------------------------------------------------------------------------
$S = 'MAIL';
$sp = (string) ini_get('sendmail_path');
chk($S, 'INFO', 'sendmail_path', $sp !== '' ? redact($sp) : '(empty)');
$spBin = $sp !== '' ? preg_split('/\s+/', trim($sp)) : array('');
$spBin = $spBin[0];
if ($spBin !== '' && obdAllows($spBin)) {
    chk($S, is_executable($spBin) ? 'OK' : 'WARN', 'sendmail binary', $spBin . '  executable=' . yn(is_executable($spBin)), 'mail() will fail; use SMTP.');
}
chk($S, 'INFO', 'SMTP / smtp_port (Windows-only for mail())', (ini_get('SMTP') ?: '(unset)') . ':' . (ini_get('smtp_port') ?: '?'));
chk($S, fnUsable('mail') ? 'OK' : 'WARN', 'mail() usable', yn(fnUsable('mail')), 'Use an SMTP library with the hoster mailbox credentials.');
chk($S, 'INFO', 'outbound SMTP ports', 'not tested (would contact third parties); ask the hoster whether ports 25/465/587 are open');

// --- network -----------------------------------------------------------------------------
$S = 'NETWORK';
$host = 'www.google.com';
if (fnUsable('gethostbyname')) {
    $t0 = microtime(true);
    $ip = @gethostbyname($host);
    $ms = round((microtime(true) - $t0) * 1000);
    chk($S, $ip !== $host ? 'OK' : 'FAIL', 'DNS resolution', $host . ' -> ' . ($ip !== $host ? $ip : 'failed') . ' (' . $ms . ' ms)', 'No DNS from PHP: outbound requests and update checks will fail.');
} else {
    chk($S, 'WARN', 'DNS resolution', 'gethostbyname disabled');
}
$httpsOk = false;
if (extension_loaded('openssl') && in_array('https', stream_get_wrappers(), true) && ini_get('allow_url_fopen')) {
    $ctx = stream_context_create(array('http' => array('timeout' => 4, 'method' => 'HEAD', 'ignore_errors' => true, 'follow_location' => 0)));
    $t0 = microtime(true);
    $h = fnUsable('get_headers') ? @get_headers('https://www.google.com/generate_204', 0, $ctx) : false;
    $ms = round((microtime(true) - $t0) * 1000);
    $httpsOk = (bool) $h;
    chk($S, $h ? 'OK' : 'WARN', 'outbound HTTPS via streams (4s timeout)', $h ? $h[0] . ' (' . $ms . ' ms)' : 'blocked, no route or timeout', 'Outbound HTTPS blocked: no webhooks, API calls or update checks.');
} else {
    chk($S, 'WARN', 'outbound HTTPS via streams', 'unavailable (allow_url_fopen off, or no openssl/https wrapper)');
}
if (function_exists('curl_init') && fnUsable('curl_exec')) {
    $ch = curl_init('https://www.google.com/generate_204');
    curl_setopt_array($ch, array(CURLOPT_NOBODY => true, CURLOPT_TIMEOUT => 4, CURLOPT_CONNECTTIMEOUT => 3, CURLOPT_RETURNTRANSFER => true));
    $okc = curl_exec($ch) !== false;
    $code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $cerr = curl_error($ch);
    if (PHP_VERSION_ID < 80000) { curl_close($ch); }   // no-op since 8.0, deprecated in 8.5
    chk($S, $okc ? 'OK' : 'WARN', 'outbound HTTPS via cURL (4s timeout)', $okc ? 'HTTP ' . $code : $cerr, 'cURL cannot reach the internet; check CA bundle (curl.cainfo/openssl.cafile) and firewall.');
} else {
    chk($S, 'INFO', 'outbound HTTPS via cURL', 'curl unavailable');
}
chk($S, 'INFO', 'CA bundle', 'openssl.cafile=' . (ini_get('openssl.cafile') ?: '(default)') . ' curl.cainfo=' . (ini_get('curl.cainfo') ?: '(default)'));

// --- CLI / cron --------------------------------------------------------------------------
$S = 'CLI / CRON';
if (fnUsable('exec') && fnUsable('escapeshellarg')) {
    $found = array();
    $globs = array('/usr/bin/php*', '/usr/local/bin/php*', '/opt/plesk/php/*/bin/php', '/opt/php*/bin/php', '/opt/alt/php*/usr/bin/php', '/opt/cpanel/ea-php*/root/usr/bin/php', '/usr/local/php*/bin/php');
    foreach ($globs as $g) {
        foreach ((fnUsable('glob') ? (array) glob($g) : array()) as $p) {
            if (is_string($p) && $p !== '' && obdAllows($p) && is_file($p) && is_executable($p) && !preg_match('/(config|ize|cgi|fpm|dbg)/', basename($p))) { $found[$p] = true; }
        }
    }
    $list = array();
    foreach (array_keys($found) as $p) {
        $out = array(); $rc = 1;
        @exec(escapeshellarg($p) . ' -v 2>&1', $out, $rc);
        $first = isset($out[0]) ? $out[0] : '';
        if (preg_match('/^PHP (\S+)/', $first, $mm)) { $list[] = $p . ' = ' . $mm[1]; }
    }
    chk($S, $list ? 'OK' : 'INFO', 'php CLI binaries found', $list ? implode('  ', $list) : '(none found in common locations; ask the hoster which php cron uses)');
    $ct = array(); $rc = 1;
    @exec('command -v crontab 2>/dev/null', $ct, $rc);
    chk($S, $rc === 0 ? 'OK' : 'INFO', 'crontab binary', $rc === 0 && $ct ? $ct[0] : 'not found (cron is probably managed via the panel)', 'Use the hoster panel cron/scheduled tasks and a URL or php CLI call.');
} else {
    chk($S, 'INFO', 'php CLI discovery', 'skipped: exec() unavailable');
    $guess = array();
    foreach (array('/usr/bin/php', '/usr/local/bin/php') as $p) { if (obdAllows($p) && @is_executable($p)) { $guess[] = $p; } }
    chk($S, 'INFO', 'php binaries (is_executable guess)', $guess ? implode(', ', $guess) : '(none visible; paths outside open_basedir cannot be tested)');
    chk($S, 'INFO', 'crontab', 'cannot check without exec(); look for a cron/scheduled-task feature in the hoster panel');
}
chk($S, 'INFO', 'cron hint', 'PHP_BINARY=' . (PHP_BINARY !== '' ? PHP_BINARY : '(empty)') . ' (if php-fpm/php-cgi, the cron CLI binary differs)');

// --- cleanup, warnings -------------------------------------------------------------------
foreach (array_unique($created) as $f) {
    if (is_file($f)) { @unlink($f); }
    elseif (is_dir($f)) { @rmdir($f); }
}
$leftover = array();
foreach (array_unique($created) as $f) { if (file_exists($f)) { $leftover[] = $f; } }
if ($leftover) { chk('CLEANUP', 'WARN', 'temp files not removed', implode(', ', $leftover), 'Delete these files manually.'); }
restore_error_handler();
$S = 'PHP WARNINGS DURING THIS REPORT';
foreach (array_unique($phpWarnings) as $w) { chk($S, 'INFO', 'warning', $w); }

// --- summary -----------------------------------------------------------------------------
$counts = array('OK' => 0, 'WARN' => 0, 'FAIL' => 0, 'INFO' => 0);
foreach ($checks as $c) { $counts[$c['status']]++; }
$problems = array();
foreach ($checks as $c) { if ($c['status'] === 'WARN' || $c['status'] === 'FAIL') { $problems[] = $c; } }

if (isset($_GET['format']) && $_GET['format'] === 'json') {
    header('Content-Type: application/json; charset=utf-8');
    $out = array('generated' => date('c'), 'file' => basename(__FILE__), 'summary' => $counts, 'checks' => $checks, 'problems' => $problems, 'reminder' => 'Delete this file from the web root.');
    if ($verbose) { $out['raw'] = $raw; }
    echo json_encode($out, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE | JSON_PARTIAL_OUTPUT_ON_ERROR), "\n";
    exit;
}

header('Content-Type: text/plain; charset=utf-8');
echo 'test-webspace-php-environment ' . date('c') . "\n";
$cur = null;
foreach ($checks as $c) {
    if ($c['section'] !== $cur) { $cur = $c['section']; echo "\n== " . $cur . " ==\n"; }
    echo '[' . str_pad($c['status'], 4, ' ', STR_PAD_BOTH) . '] ' . str_pad($c['label'], 38) . ' ' . $c['detail'] . "\n";
}
if ($verbose) {
    echo "\n== RAW (verbose) ==\n";
    echo json_encode($raw, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE | JSON_PARTIAL_OUTPUT_ON_ERROR) . "\n";
}
echo "\n== SUMMARY ==\n";
echo 'OK=' . $counts['OK'] . '  WARN=' . $counts['WARN'] . '  FAIL=' . $counts['FAIL'] . '  INFO=' . $counts['INFO'] . "\n";
foreach ($problems as $c) {
    echo '[' . str_pad($c['status'], 4, ' ', STR_PAD_BOTH) . '] ' . $c['label'] . ': ' . $c['detail'] . ($c['hint'] !== '' ? "\n         -> " . $c['hint'] : '') . "\n";
}
if (!$problems) { echo "No warnings or failures.\n"; }
echo "\n**DELETE THIS FILE FROM THE WEB ROOT NOW. It reveals server paths and configuration.**\n";
