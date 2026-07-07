# additions for php web pages / debugging 


What I would add before working on it:

  - php8.5-cli
  - php8.5-fpm or php-cgi
  - PHP extensions: php8.5-mbstring, php8.5-xml, php8.5-curl, php8.5-zip, php8.5-gd, php8.5-intl, php8.5-opcache
  - php8.5-xdebug for local debugging
  - composer
  - Static analysis and refactor tools: phpstan, rector, php-cs-fixer or phpcs, phpunit
  - Browser tooling: chromium or firefox, plus playwright if you want repeatable UI checks
  - Optional asset tools: imagemagick, exiftool

## project php settings recommendation 

**keep `short_open_tag` off (default)**

`<?= ... ?>` is safe either way and stays available even when `short_open_tag` is off. The main downside is portability: the code will break or leak source on any environment where that setting is off. Another downside is XML compatibility: `<?xml ... ?>` can be confused with a PHP short tag when short tags are enabled.

**recommended packages**

- php8.5-cli
- php8.5-cgi
- php8.5-xml
- php8.5-mbstring
- php8.5-curl
- php8.5-gd
- php8.5-zip
- php8.5-opcache
- php8.5-xdebug
- composer
