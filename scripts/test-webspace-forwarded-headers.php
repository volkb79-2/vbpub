<?php
/**
 * test-webspace-forwarded-headers.php - forwarding-header probe for shared webspace.
 *
 * PURPOSE
 *   Answers "is a front-end proxy before PHP, and does it preserve, append, overwrite or strip
 *   client-supplied forwarding headers?" It reports exactly what this PHP request received and
 *   interprets the X-Forwarded-For chain and X-Forwarded-Proto for a trusted-proxy hop count.
 *   For the rest of the PHP environment use test-webspace-php-environment.php; for Apache
 *   behaviour use test-webspace-htaccess-support.php.
 *
 * USAGE
 *   1. Set $TOKEN below to a long random string, e.g. from `openssl rand -hex 16`.
 *   2. Upload this single file into the web root of the hoster.
 *   3. Run it twice from the same client:
 *        plain:
 *          https://DOMAIN/test-webspace-forwarded-headers.php?token=YOUR-TOKEN
 *        forged (send the TEST-NET-3 marker so the probe can identify it):
 *          curl -H 'X-Forwarded-For: 203.0.113.9' \
 *               -H 'X-Forwarded-Proto: http' \
 *               'https://DOMAIN/test-webspace-forwarded-headers.php?token=YOUR-TOKEN'
 *      Optional parameters:
 *        &format=json      same data, structured (save it and send it back)
 *   4. Compare the plain and forged outputs, then DELETE THE FILE AGAIN. It reveals proxy topology
 *      details and the hosting server's view of request addresses.
 *
 * WHAT IT REPORTS
 *   The explicitly requested PHP server fields: REMOTE_ADDR, HTTP_X_FORWARDED_FOR,
 *   HTTP_X_FORWARDED_PROTO, HTTP_X_FORWARDED_HOST, HTTP_X_FORWARDED_PORT, HTTP_X_REAL_IP,
 *   HTTP_FORWARDED, HTTP_HOST, HTTPS, SERVER_PORT, REQUEST_SCHEME, SERVER_SOFTWARE and PHP_SAPI.
 *   It also lists every other HTTP header whose name matches forward|real|client|proxy|via|cdn|cf-.
 *   Cookie and authorization headers are never listed. The access token is replaced with *** if
 *   it appears in any value. C0 control bytes and DEL in values and reflected CGI/header names
 *   are shown as \xNN. The whole environment is never dumped.
 *
 * VERDICT LOGIC
 *   X-Forwarded-For is split on commas into a chain, in the order PHP received it. Only the
 *   deliberate 203.0.113.0/24 (IPv4 TEST-NET-3) marker can drive a trusted-hop recommendation.
 *   An unmarked multi-entry chain is diagnostic only: plain requests can legitimately carry
 *   multi-entry chains, so it is neither proof of forgery nor a basis for a trusted-hop count.
 *
 *   PHP can only observe the post-proxy header, not what was sent. The TEST-NET-3 value is the
 *   strong check: if it survives, the proxy did not remove or overwrite that client-supplied
 *   prefix. A chain longer than one entry is append evidence. The trusted-hop recommendation is:
 *     - one TEST-NET-3 entry, or TEST-NET-3 at the chain end: trusted hops: 0 (the header has no
 *       proxy view after the forged value, so forwarded client identity must not be trusted);
 *     - TEST-NET-3 followed by another entry: trusted hops: the number of entries after the
 *       marker, because each one is append evidence at the marker's live position in the chain;
 *     - no TEST-NET-3 marker, including an unmarked multi-entry chain:
 *       UNDETERMINED: run again with the forged header.
 *   A single forged value reaching PHP with no later entry is indistinguishable from a direct
 *   request at PHP's boundary; the safe recommendation is therefore 0, not "there is no proxy".
 *
 *   For X-Forwarded-Proto, HTTPS-on plus a forged value containing "http" means the forged value
 *   survived. HTTPS-on plus a present HTTPS-only value means it was overwritten to https. A
 *   missing header cannot be classified from one request: it may have been stripped, or simply
 *   not added. Compare the plain and forged runs.
 *
 * APP-LEVEL CHECK (AUTHORITATIVE AFTER THE NAF TARBALL IS DEPLOYED)
 *   PHP and Passenger may be fronted differently, so this script is not the final authority for
 *   naf. After deploying the naf tarball, use the app's own GET /api/myip endpoint, which returns
 *   "ip" as the app sees it plus the raw X-Forwarded-For and X-Real-IP headers. Repeat both the
 *   plain and forged requests against /api/myip and compare those values. Set naf's trusted-proxy
 *   hop count only from that app-level evidence; keep it at 0 until then.
 *
 * WHAT IT TOUCHES
 *   Reads request metadata only. No network calls, no files written, no headers modified, and no
 *   environment dump. Runs on PHP 7.2+ (no PHP 8-only syntax).
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
if (function_exists('ini_set')) { @ini_set('display_errors', '0'); }

/**
 * Turn any server value or reflected name into printable text while never exposing the access
 * token. Every C0 control byte and DEL is rendered as \xNN rather than passed to the terminal.
 */
function forwardedCleanValue($value, $token)
{
    if (is_array($value)) {
        $value = json_encode($value, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_SUBSTITUTE);
    }
    $text = (string) $value;
    if ($token !== '' && strlen($token) >= 16) {
        $text = str_replace($token, '***', $text);
    }
    return preg_replace_callback(
        '/[\x00-\x1F\x7F]/',
        function ($match) { return sprintf('\\x%02X', ord($match[0])); },
        $text
    );
}

function forwardedHeaderName($serverKey)
{
    $headerPart = str_replace('_', '-', substr($serverKey, 5));
    $parts = explode('-', strtolower($headerPart));
    foreach ($parts as $index => $part) {
        $parts[$index] = in_array($part, array('x', 'cf', 'cdn', 'ip', 'http', 'https'), true) ? strtoupper($part) : ucfirst($part);
    }
    return implode('-', $parts);
}

function forwardedDisplayName($serverKey)
{
    if (strpos($serverKey, 'HTTP_') === 0) {
        return forwardedHeaderName($serverKey);
    }
    return $serverKey;
}

function forwardedAddressKey($value)
{
    $value = trim((string) $value);
    if (preg_match('/^\[([^\]]+)\](?::[0-9]+)?$/', $value, $match)) {
        return strtolower($match[1]);
    }
    if (preg_match('/^((?:[0-9]{1,3}\.){3}[0-9]{1,3}):[0-9]+$/', $value, $match)) {
        return $match[1];
    }
    return strtolower($value);
}

function forwardedIsTestNet3($value)
{
    $value = trim((string) $value);
    return preg_match(
        '/^203\.0\.113\.(?:25[0-5]|2[0-4][0-9]|[0-9]{1,2})(?::[0-9]{1,5})?$/',
        $value
    ) === 1;
}

$requiredFields = array(
    'REMOTE_ADDR',
    'HTTP_X_FORWARDED_FOR',
    'HTTP_X_FORWARDED_PROTO',
    'HTTP_X_FORWARDED_HOST',
    'HTTP_X_FORWARDED_PORT',
    'HTTP_X_REAL_IP',
    'HTTP_FORWARDED',
    'HTTP_HOST',
    'HTTPS',
    'SERVER_PORT',
    'REQUEST_SCHEME',
    'SERVER_SOFTWARE',
    'PHP_SAPI',
);

$serverRows = array();
foreach ($requiredFields as $key) {
    if ($key === 'PHP_SAPI') {
        $serverRows[] = array(
            'name' => forwardedCleanValue('PHP_SAPI', $TOKEN),
            'key' => forwardedCleanValue('PHP_SAPI', $TOKEN),
            'kind' => 'requested',
            'present' => true,
            'value' => forwardedCleanValue(PHP_SAPI, $TOKEN),
        );
        continue;
    }
    $present = array_key_exists($key, $_SERVER);
    $serverRows[] = array(
        'name' => forwardedCleanValue(forwardedDisplayName($key), $TOKEN),
        'key' => forwardedCleanValue($key, $TOKEN),
        'kind' => 'requested',
        'present' => $present,
        'value' => $present ? forwardedCleanValue($_SERVER[$key], $TOKEN) : null,
    );
}

$extraHeaders = array();
foreach ($_SERVER as $key => $value) {
    if (!is_string($key) || strpos($key, 'HTTP_') !== 0 || in_array($key, $requiredFields, true)) {
        continue;
    }
    $headerName = forwardedHeaderName($key);
    if (preg_match('/authorization|cookie|set-cookie/i', $key)) {
        continue;
    }
    if (!preg_match('/forward|real|client|proxy|via|cdn|cf-/i', $headerName)) {
        continue;
    }
    $extraHeaders[] = array(
        'name' => forwardedCleanValue($headerName, $TOKEN),
        'key' => forwardedCleanValue($key, $TOKEN),
        'kind' => 'matched-header',
        'present' => true,
        'value' => forwardedCleanValue($value, $TOKEN),
    );
}
usort($extraHeaders, function ($left, $right) {
    return strcmp($left['name'], $right['name']);
});
$serverRows = array_merge($serverRows, $extraHeaders);

$remoteAddr = isset($_SERVER['REMOTE_ADDR']) ? (string) $_SERVER['REMOTE_ADDR'] : '';
$xffPresent = array_key_exists('HTTP_X_FORWARDED_FOR', $_SERVER);
$xffRaw = $xffPresent ? (string) $_SERVER['HTTP_X_FORWARDED_FOR'] : '';
$xffEntries = array();
if ($xffPresent && trim($xffRaw) !== '') {
    foreach (preg_split('/\s*,\s*/', trim($xffRaw)) as $entry) {
        if ($entry !== '') {
            $xffEntries[] = $entry;
        }
    }
}
$xffChainLength = count($xffEntries);
$xffLast = $xffChainLength > 0 ? $xffEntries[$xffChainLength - 1] : '';
$xffLastKey = forwardedAddressKey($xffLast);
$testNet3Present = false;
$testNet3Index = null;
$valueDiffersFromChainEnd = false;
foreach ($xffEntries as $index => $entry) {
    if ($testNet3Index === null && forwardedIsTestNet3($entry)) {
        $testNet3Present = true;
        $testNet3Index = $index;
    }
    if ($index < $xffChainLength - 1 && forwardedAddressKey($entry) !== $xffLastKey) {
        $valueDiffersFromChainEnd = true;
    }
}
$testNet3AtChainEnd = $testNet3Present && forwardedIsTestNet3($xffLast);
$entriesAfterTestNet3 = $testNet3Index === null ? 0 : $xffChainLength - $testNet3Index - 1;
$proxyAppendEvidence = $xffChainLength > 1 && $xffLast !== '';
$lastMatchesRemoteAddr = $xffLast !== '' && $remoteAddr !== '' && $xffLastKey === forwardedAddressKey($remoteAddr);
$lastDiffersFromRemoteAddr = $xffLast !== '' && $remoteAddr !== '' && $xffLastKey !== forwardedAddressKey($remoteAddr);

$xfpPresent = array_key_exists('HTTP_X_FORWARDED_PROTO', $_SERVER);
$xfpRaw = $xfpPresent ? (string) $_SERVER['HTTP_X_FORWARDED_PROTO'] : '';
$xfpProtocols = array();
if ($xfpPresent && trim($xfpRaw) !== '') {
    foreach (preg_split('/\s*,\s*/', strtolower(trim($xfpRaw))) as $protocol) {
        if ($protocol !== '') {
            $xfpProtocols[] = $protocol;
        }
    }
}
$httpsByServerFlag = isset($_SERVER['HTTPS']) && !in_array(strtolower((string) $_SERVER['HTTPS']), array('', '0', 'off', 'false', 'no'), true);
$httpsByScheme = isset($_SERVER['REQUEST_SCHEME']) && strtolower((string) $_SERVER['REQUEST_SCHEME']) === 'https';
$httpsByPort = isset($_SERVER['SERVER_PORT']) && (string) $_SERVER['SERVER_PORT'] === '443';
$httpsObserved = $httpsByServerFlag || $httpsByScheme || $httpsByPort;
$xfpContainsHttp = in_array('http', $xfpProtocols, true);
$xfpContainsHttps = in_array('https', $xfpProtocols, true);
$forgedHttpPreserved = $httpsObserved && $xfpContainsHttp;
$xfpOverwrittenToHttps = $httpsObserved && $xfpPresent && $xfpContainsHttps && !$xfpContainsHttp;

$trustedHops = null;
$recommendation = 'UNDETERMINED: run again with the forged header.';
$reason = 'No deliberate TEST-NET-3 marker was observed; an unmarked chain is diagnostic only.';
if (!$xffPresent || $xffChainLength === 0) {
    $reason = 'X-Forwarded-For is absent or empty and has no TEST-NET-3 marker; one request cannot distinguish a missing proxy from a proxy that strips the header.';
} elseif (!$testNet3Present) {
    $reason = 'X-Forwarded-For has no deliberate TEST-NET-3 marker; ' . $xffChainLength . ' chain entr' . ($xffChainLength === 1 ? 'y is' : 'ies are') . ' diagnostic only.';
} elseif ($testNet3AtChainEnd) {
    $trustedHops = 0;
    $recommendation = 'trusted hops: 0';
    $reason = 'TEST-NET-3 is the final or only X-Forwarded-For entry; no later proxy view was observed, so do not trust the forwarded client identity.';
} elseif ($entriesAfterTestNet3 > 0) {
    $trustedHops = $entriesAfterTestNet3;
    $recommendation = 'trusted hops: ' . $trustedHops;
    $reason = 'TEST-NET-3 is at position ' . ($testNet3Index + 1) . ' of ' . $xffChainLength . '; the ' . $entriesAfterTestNet3 . ' later entr' . ($entriesAfterTestNet3 === 1 ? 'y provides' : 'ies provide') . ' append evidence.';
} else {
    $reason = 'The TEST-NET-3 marker has no later entry to establish append evidence.';
}
if ($forgedHttpPreserved) {
    $reason .= ' X-Forwarded-Proto still says http while PHP reports HTTPS.';
} elseif ($xfpOverwrittenToHttps) {
    $reason .= ' X-Forwarded-Proto was overwritten or normalised to https.';
}

$report = array(
    'generated' => date('c'),
    'file' => basename(__FILE__),
    'php_version' => PHP_VERSION,
    'server' => $serverRows,
    'xff' => array(
        'present' => $xffPresent,
        'value' => $xffPresent ? forwardedCleanValue($xffRaw, $TOKEN) : null,
        'entries' => array_map(function ($entry) use ($TOKEN) { return forwardedCleanValue($entry, $TOKEN); }, $xffEntries),
        'chain_length' => $xffChainLength,
        'test_net_3_present' => $testNet3Present,
        'test_net_3_position' => $testNet3Index === null ? null : $testNet3Index + 1,
        'entries_after_test_net_3' => $entriesAfterTestNet3,
        'test_net_3_at_chain_end' => $testNet3AtChainEnd,
        'value_differs_from_chain_end_diagnostic' => $valueDiffersFromChainEnd,
        'proxy_append_evidence' => $proxyAppendEvidence,
        'last_entry_matches_remote_addr' => $lastMatchesRemoteAddr,
        'last_entry_differs_from_remote_addr' => $lastDiffersFromRemoteAddr,
    ),
    'x_forwarded_proto' => array(
        'https_observed_by_php' => $httpsObserved,
        'https_tests' => array(
            'HTTPS' => $httpsByServerFlag,
            'REQUEST_SCHEME' => $httpsByScheme,
            'SERVER_PORT_443' => $httpsByPort,
        ),
        'present' => $xfpPresent,
        'value' => $xfpPresent ? forwardedCleanValue($xfpRaw, $TOKEN) : null,
        'forged_http_preserved' => $forgedHttpPreserved,
        'overwritten_to_https' => $xfpOverwrittenToHttps,
    ),
    'verdict' => array(
        'trusted_hops' => $trustedHops,
        'recommendation' => $recommendation,
        'reasoning' => $reason,
        'authoritative_check' => 'Repeat after deploying naf: GET /api/myip plain and forged; its ip and raw forwarding-header values decide.',
    ),
    'reminder' => 'Delete this file from the web root.',
);

if (isset($_GET['format']) && is_string($_GET['format']) && $_GET['format'] === 'json') {
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode($report, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_SUBSTITUTE | JSON_PARTIAL_OUTPUT_ON_ERROR) . "\n";
    exit;
}

header('Content-Type: text/plain; charset=utf-8');
echo 'test-webspace-forwarded-headers ' . $report['generated'] . "\n";
echo "\n== PHP VIEW OF FORWARDING FIELDS ==\n";
foreach ($report['server'] as $row) {
    $value = $row['present'] ? ($row['value'] === '' ? '(empty string)' : $row['value']) : '(unset)';
    $label = $row['name'] . ($row['kind'] === 'matched-header' ? ' [' . $row['key'] . ']' : '');
    echo str_pad($label, 42) . ' ' . $value . "\n";
}

echo "\n== VERDICT ==\n";
echo 'X-Forwarded-For present: ' . ($report['xff']['present'] ? 'yes' : 'no') . '  chain length: ' . $report['xff']['chain_length'] . "\n";
echo 'TEST-NET-3 value reaches PHP: ' . ($report['xff']['test_net_3_present'] ? 'yes' : 'no') . "\n";
echo 'TEST-NET-3 position / entries after: ' . ($report['xff']['test_net_3_position'] === null ? '(none)' : $report['xff']['test_net_3_position']) . ' / ' . $report['xff']['entries_after_test_net_3'] . "\n";
echo 'Value differs from chain end (diagnostic only): ' . ($report['xff']['value_differs_from_chain_end_diagnostic'] ? 'yes' : 'no') . "\n";
echo 'Proxy append evidence: ' . ($report['xff']['proxy_append_evidence'] ? 'yes' : 'no');
echo '  last matches REMOTE_ADDR: ' . ($report['xff']['last_entry_matches_remote_addr'] ? 'yes' : 'no');
echo '  last differs: ' . ($report['xff']['last_entry_differs_from_remote_addr'] ? 'yes' : 'no') . "\n";
echo 'PHP reports HTTPS: ' . ($report['x_forwarded_proto']['https_observed_by_php'] ? 'yes' : 'no');
echo '  forged X-Forwarded-Proto=http preserved: ' . ($report['x_forwarded_proto']['forged_http_preserved'] ? 'yes' : 'no');
echo '  overwritten to https: ' . ($report['x_forwarded_proto']['overwritten_to_https'] ? 'yes' : 'no') . "\n";
echo 'recommendation: ' . $report['verdict']['recommendation'] . "\n";
echo 'reasoning: ' . $report['verdict']['reasoning'] . "\n";
echo "\nAuthoritative check after naf is deployed: GET /api/myip plain and forged; compare its ip and raw forwarding-header values.\n";
echo "\n**DELETE THIS FILE FROM THE WEB ROOT NOW. It reveals proxy topology details.**\n";
