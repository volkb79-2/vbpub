# Devcontainer Manifest — trixie-py3.14-20260714-5

## Release

- Build date: `20260714-5`
- Target: `unknown`
- Debian: `trixie`
- Python: `3.14`
- Immutable image tag: `trixie-py3.14-20260714-5`
- Floating image tag: `trixie-py3.14-latest`

## Pull

```bash
docker pull ghcr.io/volkb79-2/modern-debian-tools-python-debug:trixie-py3.14-20260714-5
```

## Base

- Debian: trixie
- Python: 3.14
- Image version: 20260714-5
- Image tag: trixie-py3.14-20260714-5
- Devcontainers release: unknown
- Devcontainers image version: unknown

## Purpose

Modern Debian Tools + Python Debug + PHP 8.5 base image. Adds PHP 8.5 CLI/FPM, composer, Xdebug, and the common PHP extensions needed for web debugging. Docs: https://github.com/volkb79-2/vbpub/blob/main/modern-debian-tools-python-debug/package-manifests-versioned/modern-debian-tools-python-debug/trixie-py3.14-php8.5-20260714-5.md

## Node Runtime (source: nodesource apt repo (https://deb.nodesource.com))

- Node.js: `v26.5.0`
- npm: `11.17.0`

## Python & PHP Runtime (source: Debian trixie apt / sury.org PHP repo (https://packages.sury.org/php))

- PHP: `PHP 8.5.8 (cli) (built: Jul  3 2026 10:04:35) (NTS)`
- Composer: `PHP Deprecated:  Case statements followed by a semicolon (;) are deprecated, use a colon (:) instead in /usr/share/php/React/Promise/functions.php on line 300`

### PHP Extensions (loaded modules)

- `calendar`
- `Core`
- `ctype`
- `curl`
- `date`
- `dom`
- `exif`
- `FFI`
- `fileinfo`
- `filter`
- `ftp`
- `gd`
- `gettext`
- `hash`
- `iconv`
- `intl`
- `json`
- `lexbor`
- `libxml`
- `mbstring`
- `openssl`
- `pcntl`
- `pcre`
- `PDO`
- `Phar`
- `posix`
- `random`
- `readline`
- `Reflection`
- `session`
- `shmop`
- `SimpleXML`
- `sockets`
- `sodium`
- `SPL`
- `standard`
- `sysvmsg`
- `sysvsem`
- `sysvshm`
- `tokenizer`
- `uri`
- `xdebug`
- `xml`
- `xmlreader`
- `xmlwriter`
- `xsl`
- `Zend OPcache`
- `zip`
- `zlib`
- `[Zend Modules]`
- `Xdebug`
- `Zend OPcache`

## First-Party Wheels (source: built from source, installed via pip)

| Tool | Version | Policy | Project Home | Package digest |
|---|---|---|---|---|
| `CIU` | `4.5.0` | latest |  |  |
| `cmru` | `1.3.0` | latest |  |  |

## AI CLI Tools (source: npm / PyPI / GitHub Releases)

**Version policy:** latest npm/GitHub release at build time (override via build arg). AI CLI tool versions are resolved dynamically during `stage_tool_artifacts` from the respective package registries (npm, PyPI, GitHub Releases).

| Tool | Version | Policy | Project Home | Package digest |
|---|---|---|---|---|
| `aider` | `aider 0.86.3.dev53+g5dc9490bb` | latest | https://github.com/Aider-AI/aider |  |
| `antigravity` | `1.1.2` | latest | https://github.com/antigravity/antigravity-cli | `sha256:0754010347926daf00c96734…` |
| `claude` | `2.1.209 (Claude Code)` | latest | https://github.com/anthropics/claude-code | `sha256:b882f4b8b27772f897540df5…` |
| `codex` | `codex-cli 0.144.4` | latest | https://github.com/openai/codex |  |
| `copilot` | `GitHub Copilot CLI 1.0.70.` | latest | https://github.com/github/copilot-cli |  |
| `openclaw` | `OpenClaw 2026.7.1 (2d2ddc4)` | latest | https://github.com/openclaw/openclaw |  |
| `opencode` | `1.17.20` | latest | https://github.com/anomalyco/opencode |  |
| `reasonix` | `reasonix v1.17.12` | latest | https://github.com/reasonix/reasonix |  |

## Container Inspection Tools (source: GitHub Releases (pre-built binaries))

**Version policy:** latest GitHub release at build time (override via build arg). All tools in this category are downloaded as pre-built binaries from their upstream releases.

| Tool | Version | Policy | Project Home | Package digest |
|---|---|---|---|---|
| `dive` | `dive 0.13.1` | latest | https://github.com/wagoodman/dive | `sha256:0c20d18f0cc87e6e982a3289…` |
| `dtop` | `dtop 0.7.9` | latest | https://github.com/amir20/dtop | `sha256:d82a2fc664733561d6fe4ec9…` |
| `glances` | `Glances version:	4.5.5` | latest | https://github.com/nicolargo/glances | `sha256:7411c0fc02881fa970a5c1b0…` |
| `lazydocker` | `Version: 0.25.2` | latest | https://github.com/jesseduffield/lazydocker | `sha256:0d9dbfc26068b218e7ed84b1…` |
| `syft` | `syft 1.46.0` | latest | https://github.com/anchore/syft | `sha256:d654f678b709eb53c393d385…` |

## Security & Debug Tools (source: GitHub Releases (pre-built binaries, sha256-verified))

**Version policy:** latest GitHub release at build time (override via build arg). Binaries are verified via upstream SHA256 checksums before installation.

| Tool | Version | Policy | Project Home | Package digest |
|---|---|---|---|---|
| `cdebug` | `cdebug version 0.0.19 (built: 2026-01-18T19:11:40Z commit: 6c205f0b663df4dec235f42e905e94b40709159a)` | latest | https://github.com/iximiuz/cdebug | `sha256:10c2dd283ed690f445ac41d7…` |
| `grype` | `Application:         grype` | latest | https://github.com/anchore/grype | `sha256:3fad92940650e514c0aa2dad…` |
| `hadolint` | `Haskell Dockerfile Linter 2.14.0` | latest | https://github.com/hadolint/hadolint | `sha256:6bf226944684f56c84dd014e…` |

## Custom Tooling (source: GitHub Releases / Debian apt)

**Version policy:** latest GitHub release at build time (override via build arg). Some tools are compiled from source (nvim, htop); the rest are pre-built binaries.

| Tool | Version | Policy | Project Home | Package digest |
|---|---|---|---|---|
| `awscli` | `aws-cli/2.35.22 Python/3.14.6 Linux/7.0.12+deb13-amd64 exe/x86_64.debian.13` | latest | https://github.com/aws/aws-cli | `sha256:edd9ba798acb3ef6131e5bf9…` |
| `b2` | `b2 command line tool, version 4.7.1 (b2sdk version 2.12.0)` | latest | https://github.com/Backblaze/B2_Command_Line_Tool | `sha256:0f4720858f137cbbdb434f13…` |
| `bat` | `bat 0.26.1 (979ba22)` | latest | https://github.com/sharkdp/bat | `sha256:726f04c8f576a7fd18b7634f…` |
| `composer` | `PHP Deprecated:  Case statements followed by a semicolon (;) are deprecated, use a colon (:) instead in /usr/share/php/React/Promise/functions.php on line 300` | latest |  |  |
| `consul` | `Consul v2.0.2` | latest | https://github.com/hashicorp/consul | `sha256:96e56c9d06b4a15bfa316afa…` |
| `delta` | `delta 0.19.2` | latest | https://github.com/dandavison/delta | `sha256:8e695c5f586a8c53d6c3b01b…` |
| `fd` | `fd 10.4.2` | latest | https://github.com/sharkdp/fd | `sha256:def59805cd14b5651b689908…` |
| `fzf` | `0.74.0 (6765f464)` | latest | https://github.com/junegunn/fzf | `sha256:cf919f05b7581b4c744d764e…` |
| `gh` | `gh version 2.96.0 (2026-07-02)` | latest | https://github.com/cli/cli | `sha256:83d5c2ccad5498f58bf6368a…` |
| `grpcurl` | `grpcurl v1.9.3` | latest | https://github.com/fullstorydev/grpcurl | `sha256:a926b62a85787ccf73ef8736…` |
| `htop` | `htop 3.5.1` | latest | https://github.com/htop-dev/htop | `sha256:dfc4a09845e9bc86f466a722…` |
| `nvchad` | `2.5` | latest | https://github.com/NvChad/NvChad | `sha256:738b167881a1a08880442040…` |
| `nvim` | `0.12.4` | latest | https://github.com/neovim/neovim | `sha256:012bf3fcac5ade43914df3f1…` |
| `php` | `PHP 8.5.8 (cli) (built: Jul  3 2026 10:04:35) (NTS)` | latest |  |  |
| `rga` | `ripgrep-all 0.10.10` | latest | https://github.com/phiresky/ripgrep-all | `sha256:a969c25b182ac84aa6725183…` |
| `ripgrep` | `ripgrep 15.1.0 (rev af60c2de9d)` | latest | https://github.com/BurntSushi/ripgrep | `sha256:1c9297be4a084eea7ecaedf9…` |
| `shellcheck` | `0.11.0` | latest | https://github.com/koalaman/shellcheck | `sha256:8c3be12b05d5c177a04c29e3…` |
| `vault` | `Vault v2.0.3 (7193f9a48ff6093ca61b3b627a8671e770428ba6), built 2026-06-17T12:39:45Z` | latest | https://github.com/hashicorp/vault | `sha256:1e0ffb7a82491219c7242da6…` |
| `yq` | `yq (https://github.com/mikefarah/yq/) version v4.53.3` | latest | https://github.com/mikefarah/yq | `sha256:fa52a4e758c63d38299163fb…` |

## Python Packages (source: PyPI (resolved at build time via pip))

    (installed via pip)
**Version policy:** PyPI latest at image build time (resolved via pip install). The primary venv contains full toolkit.txt closure; secondary venvs are lean (uv + debugpy + ruff only).

    aider-chat==0.86.3.dev53+g5dc9490bb
    aiohappyeyeballs==2.6.1
    aiohttp==3.13.3
    aiosignal==1.4.0
    annotated-doc==0.0.4
    annotated-types==0.7.0
    anyio==4.12.1
    asgiref==3.11.1
    ast_serialize==0.6.0
    asttokens==3.0.2
    asyncpg==0.31.0
    attrs==25.4.0
    audioop-lts==0.2.2
    backoff==2.2.1
    beautifulsoup4==4.14.3
    boolean.py==5.0
    boto3==1.43.47
    botocore==1.43.47
    build==1.5.1
    CacheControl==0.14.4
    cachetools==7.1.4
    certifi==2026.2.25
    cffi==2.0.0
    cfgv==3.5.0
    charset-normalizer==3.4.6
    check-wheel-contents==0.6.3
    ciu==4.5.0
    click==8.3.1
    cmru==1.3.0
    colorama==0.4.6
    ConfigArgParse==1.7.5
    coverage==7.15.1
    cryptography==49.0.0
    cyclonedx-python-lib==11.11.0
    debugpy==1.8.21
    decorator==5.3.1
    defusedxml==0.7.1
    diff-match-patch==20241021
    diskcache==5.6.3
    distlib==0.4.3
    distro==1.9.0
    dnspython==2.8.0
    docutils==0.23
    execnet==2.1.2
    executing==2.2.1
    fastapi==0.135.1
    fastuuid==0.14.0
    filelock==3.25.2
    flake8==7.3.0
    frozenlist==1.8.0
    fsspec==2026.2.0
    gitdb==4.0.12
    GitPython==3.1.46
    greenlet==3.5.3
    grep-ast==0.9.0
    h11==0.16.0
    hf-xet==1.4.2
    httpcore==1.0.9
    httpx==0.28.1
    huggingface_hub==1.7.1
    hvac==2.4.0
    id==1.6.1
    identify==2.6.19
    idna==3.11
    importlib_metadata==7.2.1
    importlib_resources==6.5.2
    iniconfig==2.3.0
    ipdb==0.13.13
    ipython==9.15.0
    ipython_pygments_lexers==1.1.1
    jaraco.classes==3.4.0
    jaraco.context==6.1.2
    jaraco.functools==4.6.0
    jedi==0.20.0
    jeepney==0.9.0
    Jinja2==3.1.6
    jiter==0.13.0
    jmespath==1.1.0
    json5==0.13.0
    jsonschema==4.26.0
    jsonschema-specifications==2025.9.1
    keyring==25.7.0
    librt==0.13.0
    license-expression==30.4.4
    litellm==1.82.3
    markdown-it-py==4.0.0
    MarkupSafe==3.0.3
    matplotlib-inline==0.2.2
    mccabe==0.7.0
    mdurl==0.1.2
    mixpanel==5.0.0
    more-itertools==11.1.0
    msgpack==1.2.1
    mslex==1.3.0
    multidict==6.7.1
    mypy==2.3.0
    mypy_extensions==1.1.0
    networkx==3.4.2
    nh3==0.3.6
    nodeenv==1.10.0
    numpy==2.4.6
    openai==2.28.0
    orjson==3.11.7
    oslex==0.1.3
    packageurl-python==0.17.6
    packaging==26.0
    parso==0.8.7
    pathspec==1.0.4
    pexpect==4.9.0
    pillow==12.1.1
    pip==26.1.2
    pip-api==0.0.34
    pip_audit==2.10.1
    pip-requirements-parser==32.0.1
    platformdirs==4.10.0
    pluggy==1.6.0
    posthog==7.9.12
    pre_commit==4.6.0
    prompt_toolkit==3.0.52
    propcache==0.4.1
    psutil==7.2.2
    ptyprocess==0.7.0
    pure_eval==0.2.3
    py-serializable==2.1.0
    pycodestyle==2.14.0
    pycparser==3.0
    pydantic==2.12.5
    pydantic_core==2.41.5
    pydantic-settings==2.14.2
    pydub==0.25.1
    pyflakes==3.4.0
    Pygments==2.19.2
    pypandoc==1.17
    pyparsing==3.3.2
    pyperclip==1.11.0
    pyproject-api==1.10.1
    pyproject_hooks==1.2.0
    pytest==9.1.1
    pytest-asyncio==1.4.0
    pytest-cov==7.1.0
    pytest-mock==3.15.1
    pytest-xdist==3.8.0
    python-dateutil==2.9.0.post0
    python-discovery==1.4.4
    python-dotenv==1.2.2
    PyYAML==6.0.3
    readme_renderer==45.0
    redis==8.0.1
    referencing==0.37.0
    regex==2026.2.28
    requests==2.32.5
    requests-toolbelt==1.0.0
    rfc3986==2.0.0
    rich==14.3.3
    rpds-py==0.30.0
    ruff==0.15.21
    s3transfer==0.19.1
    scipy==1.17.1
    SecretStorage==3.5.0
    setuptools==83.0.0
    setuptools-scm==10.2.0
    shellingham==1.5.4
    shtab==1.8.0
    six==1.17.0
    smmap==5.0.3
    sniffio==1.3.1
    socksio==1.0.0
    sortedcontainers==2.4.0
    sounddevice==0.5.5
    soundfile==0.13.1
    soupsieve==2.8.3
    SQLAlchemy==2.0.51
    stack-data==0.6.3
    starlette==0.52.1
    structlog==26.1.0
    tiktoken==0.12.0
    tokenizers==0.22.2
    tomli==2.4.1
    tomli_w==1.2.0
    tox==4.56.4
    tqdm==4.67.3
    traitlets==5.15.1
    tree-sitter==0.25.2
    tree-sitter-c-sharp==0.23.1
    tree-sitter-embedded-template==0.25.0
    tree-sitter-language-pack==0.13.0
    tree-sitter-yaml==0.7.2
    twine==6.2.0
    typer==0.24.1
    typing_extensions==4.15.0
    typing-inspection==0.4.2
    urllib3==2.6.3
    uv==0.11.28
    vcs-versioning==2.2.2
    virtualenv==21.6.1
    watchfiles==1.1.1
    wcwidth==0.6.0
    websockets==16.1
    wheel==0.47.0
    wheel-filename==1.4.2
    yarl==1.23.0
    zipp==3.23.0

## System Packages (source: Debian trixie apt)

    (installed via apt)
**Version policy:** Debian repository versions (prefer backports when available). System packages come from the Debian Trixie main repos; devcontainer-features are installed via the features CLI.  Exception: `skopeo` is pulled from Debian testing (pin-priority 501) for a newer version than trixie provides.

- bash-completion: `1:2.16.0-7`
- bind9-dnsutils: `1:9.20.23-1~deb13u1`
- ca-certificates: `20250419`
- composer: `2.8.8-1+deb13u3`
- curl: `8.14.1-2+deb13u4`
- fuse3: `3.17.2-3`
- gdb: `16.3-1`
- git: `1:2.47.3-0+deb13u1`
- git-lfs: `3.6.1-1+deb13u1`
- gnupg: `2.4.7-21+deb13u1`
- gzip: `1.13-1`
- httpie: `3.2.4-3`
- iputils-ping: `3:20240905-3`
- jq: `1.7.1-6+deb13u2`
- less: `668-1`
- locales: `2.41-12+deb13u3`
- lsb-release: `12.1-1`
- lsof: `4.99.4+dfsg-2`
- man-db: `2.13.1-1`
- mc: `3:4.8.33-1+deb13u1`
- nano: `8.4-1+deb13u1`
- ncdu: `1.22-1`
- netcat-openbsd: `1.229-1`
- openssl: `3.5.6-1~deb13u2`
- php8.5-cli: `8.5.8-1+0~20260703.21+debian13~1.gbp0c4f0f`
- php8.5-curl: `8.5.8-1+0~20260703.21+debian13~1.gbp0c4f0f`
- php8.5-fpm: `8.5.8-1+0~20260703.21+debian13~1.gbp0c4f0f`
- php8.5-gd: `8.5.8-1+0~20260703.21+debian13~1.gbp0c4f0f`
- php8.5-intl: `8.5.8-1+0~20260703.21+debian13~1.gbp0c4f0f`
- php8.5-mbstring: `8.5.8-1+0~20260703.21+debian13~1.gbp0c4f0f`
- php8.5-xdebug: `3.5.3-1+0~20260620.69+debian13~1.gbp8ad7a9`
- php8.5-xml: `8.5.8-1+0~20260703.21+debian13~1.gbp0c4f0f`
- php8.5-zip: `8.5.8-1+0~20260703.21+debian13~1.gbp0c4f0f`
- postgresql-client: `18+291.pgdg13+1`
- psmisc: `23.7-2`
- python3-venv: `3.13.5-1`
- redis-tools: `6:8.8.0-1rl1~trixie1`
- rsync: `3.4.1+ds1-5+deb13u4`
- skopeo: `1.22.0+ds1-1`
- sqlite3: `3.46.1-7+deb13u1`
- sshfs: `3.7.3-1.2~deb13u1`
- strace: `6.13+ds-1`
- tar: `1.35+dfsg-3.1`
- tree: `2.2.1-1`
- unzip: `6.0-29`
- vim: `2:9.1.1230-2`
- w3m: `0.5.3+git20230121-2.1`
- wget: `1.25.0-2`
- xz-utils: `5.8.1-1+deb13u1`

## Rich Documentation Links

- Family overview: https://github.com/volkb79-2/vbpub/blob/main/modern-debian-tools-python-debug/package-manifests-versioned/modern-debian-tools-python-debug/README.md
- This release page: https://github.com/volkb79-2/vbpub/blob/main/modern-debian-tools-python-debug/package-manifests-versioned/modern-debian-tools-python-debug/trixie-py3.14-20260714-5.md
- Source tree: https://github.com/volkb79-2/vbpub/tree/main/modern-debian-tools-python-debug

## In-Image File

- Devcontainer manifest: `/usr/local/share/modern-debian-tools-python-debug/manifest.md`

## Notes

This repository-hosted page exists because GHCR package descriptions render as flattened plain text.
The image labels therefore point to GitHub-hosted Markdown for richer, package-specific release notes.
The same manifest content is installed in-image at `/usr/local/share/modern-debian-tools-python-debug/manifest.md`.
