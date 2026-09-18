# Image Manifest — trixie-py3.14-php8.5-20260918

## Release

- Build date: `20260918`
- Built at (UTC): `2026-09-18T02:25:26Z`
- Debian: `trixie`
- Python: `3.14`
- Immutable image tag: `trixie-py3.14-php8.5-20260918`
- Floating image tag: `trixie-py3.14-php8.5-latest`

## Pull

```bash
docker pull ghcr.io/volkb79-2/modern-debian-tools-python-debug:trixie-py3.14-php8.5-20260918
```

## Base

- Debian: trixie
- Python: 3.14
- Image version: 20260918
- Image tag: trixie-py3.14-php8.5-20260918

## Purpose

Modern Debian Tools + Python Debug + PHP 8.5 base image. Adds PHP 8.5 CLI/FPM, composer, Xdebug, and the common PHP extensions needed for web debugging. Docs: https://github.com/volkb79-2/vbpub/blob/main/modern-debian-tools-python-debug/package-manifests-versioned/modern-debian-tools-python-debug/trixie-py3.14-php8.5-20260918.md

## Node Runtime (source: nodesource apt repo (https://deb.nodesource.com))

- Node.js: `v26.9.0`
- npm: `11.19.1`

## Python & PHP Runtime (source: Debian trixie apt / sury.org PHP repo (https://packages.sury.org/php))

- PHP: `PHP 8.5.10 (cli) (built: Aug 28 2026 07:24:42) (NTS)`
- Composer: `Composer version 2.8.8 2025-04-04 16:56:46`

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

| Tool | Version | Policy | Project Home | Package digest / source |
|---|---|---|---|---|
| `CIU` | `7.14.0` | latest |  |  |
| `cmru` | `5.3.0` | latest |  |  |

## AI CLI Tools (source: npm / PyPI / GitHub Releases)

**Version policy:** latest npm/GitHub release at build time (override via build arg). AI CLI tool versions are resolved dynamically during `stage_tool_artifacts` from the respective package registries (npm, PyPI, GitHub Releases).

| Tool | Version | Policy | Project Home | Package digest / source |
|---|---|---|---|---|
| `aider` | `aider 0.86.3.dev53+g5dc9490bb` | latest | https://github.com/Aider-AI/aider |  |
| `antigravity` | `1.2.5` | latest | https://github.com/antigravity/antigravity-cli | [`sha256:e450caab5682acc920721b04…`](https://storage.googleapis.com/antigravity-public/antigravity-cli/1.2.5-4931130160447488/linux-x64/cli_linux_x64.tar.gz) |
| `claude` | `2.1.276 (Claude Code)` | latest | https://github.com/anthropics/claude-code | [`sha256:8a56c8a14bd3cb246e2bdb7e…`](https://downloads.claude.ai/claude-code-releases/2.1.276/linux-x64/claude) |
| `claudelink` | `1.6.1` | latest | https://github.com/RBJGlobal/claudelink |  |
| `codex` | `codex-cli 0.154.0` | latest | https://github.com/openai/codex |  |
| `copilot` | `GitHub Copilot CLI 1.0.86.` | latest | https://github.com/github/copilot-cli |  |
| `openclaw` | `OpenClaw 2026.9.4 (3a9d69d)` | latest | https://github.com/openclaw/openclaw |  |
| `opencode` | `1.18.31` | latest | https://github.com/anomalyco/opencode |  |
| `pi` | `0.85.1` | latest | https://github.com/earendil-works/pi |  |
| `reasonix` | `reasonix v1.38.10` | latest | https://github.com/reasonix/reasonix |  |

## Container Inspection Tools (source: GitHub Releases (pre-built binaries))

**Version policy:** latest GitHub release at build time (override via build arg). All tools in this category are downloaded as pre-built binaries from their upstream releases.

| Tool | Version | Policy | Project Home | Package digest / source |
|---|---|---|---|---|
| `crane` | `0.22.1` | latest | https://github.com/google/go-containerregistry | [`sha256:0ab7a1d6932a213aed964ce9…`](https://github.com/google/go-containerregistry/releases/download/v0.22.1/go-containerregistry_Linux_x86_64.tar.gz) |
| `dive` | `dive 0.13.1` | latest | https://github.com/wagoodman/dive | [`sha256:0c20d18f0cc87e6e982a3289…`](https://github.com/wagoodman/dive/releases/download/v0.13.1/dive_0.13.1_linux_amd64.deb) |
| `dtop` | `dtop 0.9.3` | latest | https://github.com/amir20/dtop | [`sha256:b1f9ac8121056c9596e019d1…`](https://github.com/amir20/dtop/releases/download/v0.9.3/dtop-x86_64-unknown-linux-gnu.tar.gz) |
| `glances` | `Glances version:	4.5.6` | latest | https://github.com/nicolargo/glances | [`sha256:7c405821cb8469115e36962b…`](https://pypi.org/pypi/glances/4.5.6/json) |
| `lazydocker` | `Version: 0.25.2` | latest | https://github.com/jesseduffield/lazydocker | [`sha256:0d9dbfc26068b218e7ed84b1…`](https://github.com/jesseduffield/lazydocker/releases/download/v0.25.2/lazydocker_0.25.2_Linux_x86_64.tar.gz) |
| `lnav` | `0.14.1` | latest | https://github.com/tstack/lnav | [`sha256:ec1750f0a6962eed6bb85ad4…`](https://github.com/tstack/lnav/releases/download/v0.14.1/lnav-0.14.1-linux-musl-x86_64.zip) |
| `regctl` | `0.11.6` | latest | https://github.com/regclient/regclient | [`sha256:8e0e62a497fcdb8048d18aa9…`](https://github.com/regclient/regclient/releases/download/v0.11.6/regctl-linux-amd64) |
| `syft` | `syft 1.52.0` | latest | https://github.com/anchore/syft | [`sha256:caeedb81fb0491615f1ebd17…`](https://github.com/anchore/syft/releases/download/v1.52.0/syft_1.52.0_linux_amd64.tar.gz) |

## Security & Debug Tools (source: GitHub Releases (pre-built binaries, sha256-verified))

**Version policy:** latest GitHub release at build time (override via build arg). Binaries are verified via upstream SHA256 checksums before installation.

| Tool | Version | Policy | Project Home | Package digest / source |
|---|---|---|---|---|
| `cdebug` | `cdebug version 0.0.19 (built: 2026-01-18T19:11:40Z commit: 6c205f0b663df4dec235f42e905e94b40709159a)` | latest | https://github.com/iximiuz/cdebug | [`sha256:10c2dd283ed690f445ac41d7…`](https://github.com/iximiuz/cdebug/releases/download/v0.0.19/cdebug_linux_amd64.tar.gz) |
| `grype` | `Application:         grype` | latest | https://github.com/anchore/grype | [`sha256:3fa2dc4b924621ab65404cf0…`](https://github.com/anchore/grype/releases/download/v0.119.0/grype_0.119.0_linux_amd64.tar.gz) |
| `hadolint` | `Haskell Dockerfile Linter 2.15.1` | latest | https://github.com/hadolint/hadolint | [`sha256:c7187db94eeeeca956519a6a…`](https://github.com/hadolint/hadolint/releases/download/v2.15.1/hadolint-linux-x86_64) |

## Custom Tooling (source: GitHub Releases / Debian apt)

**Version policy:** latest GitHub release at build time (override via build arg). Some tools are compiled from source (nvim, htop); the rest are pre-built binaries.

| Tool | Version | Policy | Project Home | Package digest / source |
|---|---|---|---|---|
| `awscli` | `aws-cli/2.36.9 Python/3.14.6 Linux/7.1.8+deb13-amd64 exe/x86_64.debian.13` | latest | https://github.com/aws/aws-cli | [`sha256:9b92ccb50dfc55479ac14c4b…`](https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip) |
| `b2` | `b2 command line tool, version 5.0.0 (b2sdk version 2.13.0)` | latest | https://github.com/Backblaze/B2_Command_Line_Tool | [`sha256:1482305cddcdd3f1fb1acdbe…`](https://github.com/Backblaze/B2_Command_Line_Tool/releases/download/v5.0.0/b2-linux) |
| `bat` | `bat 0.26.1 (979ba22)` | latest | https://github.com/sharkdp/bat | [`sha256:726f04c8f576a7fd18b7634f…`](https://github.com/sharkdp/bat/releases/download/v0.26.1/bat-v0.26.1-x86_64-unknown-linux-gnu.tar.gz) |
| `composer` | `Composer version 2.8.8 2025-04-04 16:56:46` | latest |  |  |
| `consul` | `Consul v2.0.4` | latest | https://github.com/hashicorp/consul | [`sha256:7a28033850a24fd411722593…`](https://releases.hashicorp.com/consul/2.0.4/consul_2.0.4_linux_amd64.zip) |
| `delta` | `delta 0.19.2` | latest | https://github.com/dandavison/delta | [`sha256:8e695c5f586a8c53d6c3b01b…`](https://github.com/dandavison/delta/releases/download/0.19.2/delta-0.19.2-x86_64-unknown-linux-gnu.tar.gz) |
| `fd` | `fd 10.5.0` | latest | https://github.com/sharkdp/fd | [`sha256:a1259cd129636efbc3fef123…`](https://github.com/sharkdp/fd/releases/download/v10.5.0/fd-v10.5.0-x86_64-unknown-linux-gnu.tar.gz) |
| `fzf` | `0.74.4 (a140afeb)` | latest | https://github.com/junegunn/fzf | [`sha256:05e6813a337cc722c3ed07e5…`](https://github.com/junegunn/fzf/releases/download/v0.74.4/fzf-0.74.4-linux_amd64.tar.gz) |
| `gh` | `gh version 2.101.0 (2026-09-15)` | latest | https://github.com/cli/cli | [`sha256:9bca2d1c16825f109907a233…`](https://github.com/cli/cli/releases/download/v2.101.0/gh_2.101.0_linux_amd64.tar.gz) |
| `grpcurl` | `grpcurl v1.9.4` | latest | https://github.com/fullstorydev/grpcurl | [`sha256:97e13d58d2733a0e62cd2571…`](https://github.com/fullstorydev/grpcurl/releases/download/v1.9.4/grpcurl_1.9.4_linux_x86_64.tar.gz) |
| `htop` | `htop 3.5.3` | latest | https://github.com/htop-dev/htop | [`sha256:edf25ee020a5263ffbef9eef…`](https://github.com/htop-dev/htop/archive/refs/tags/3.5.3.tar.gz) |
| `ksm-optin` | `true` | latest |  |  |
| `nvchad` | `2.5` | latest | https://github.com/NvChad/NvChad | [`sha256:738b167881a1a08880442040…`](https://github.com/NvChad/NvChad/archive/refs/tags/v2.5.tar.gz) |
| `nvim` | `0.12.5` | latest | https://github.com/neovim/neovim | [`sha256:bce0f56eda1f1b1db6eee8f4…`](https://github.com/neovim/neovim/releases/download/v0.12.5/nvim-linux-x86_64.tar.gz) |
| `php` | `PHP 8.5.10 (cli) (built: Aug 28 2026 07:24:42) (NTS)` | latest |  |  |
| `rga` | `ripgrep-all 0.10.10` | latest | https://github.com/phiresky/ripgrep-all | [`sha256:a969c25b182ac84aa6725183…`](https://github.com/phiresky/ripgrep-all/releases/download/v0.10.10/ripgrep_all-v0.10.10-x86_64-unknown-linux-musl.tar.gz) |
| `ripgrep` | `ripgrep 15.2.0 (rev e89fff89ac)` | latest | https://github.com/BurntSushi/ripgrep | [`sha256:33e15bcf1624b25cdd2a5581…`](https://github.com/BurntSushi/ripgrep/releases/download/15.2.0/ripgrep-15.2.0-x86_64-unknown-linux-musl.tar.gz) |
| `shellcheck` | `0.11.0` | latest | https://github.com/koalaman/shellcheck | [`sha256:8c3be12b05d5c177a04c29e3…`](https://github.com/koalaman/shellcheck/releases/download/v0.11.0/shellcheck-v0.11.0.linux.x86_64.tar.xz) |
| `vault` | `Vault v2.1.1 (d78bbbe2d2f3d289c1ec29d431071beb23668212), built 2026-09-15T21:21:56Z` | latest | https://github.com/hashicorp/vault | [`sha256:8aa90f9cea46f541fc7baa3d…`](https://releases.hashicorp.com/vault/2.1.1/vault_2.1.1_linux_amd64.zip) |
| `yq` | `yq (https://github.com/mikefarah/yq/) version v4.53.6` | latest | https://github.com/mikefarah/yq | [`sha256:c5f056448f973ae7d39b5401…`](https://github.com/mikefarah/yq/releases/download/v4.53.6/yq_linux_amd64) |

## Python Packages (source: PyPI (resolved at build time via pip))

    (installed via pip)
**Version policy:** PyPI latest at image build time (resolved via pip install). The primary venv contains full toolkit.txt closure; secondary venvs are lean (uv + debugpy + ruff only).

    aider-chat==0.86.3.dev53+g5dc9490bb
    aiohappyeyeballs==2.6.1
    aiohttp==3.14.3
    aiosignal==1.4.0
    annotated-doc==0.0.4
    annotated-types==0.7.0
    anyio==4.12.1
    asgiref==3.11.1
    ast_serialize==0.11.2
    asttokens==3.0.2
    asyncpg==0.31.0
    attrs==25.4.0
    audioop-lts==0.2.2
    backoff==2.2.1
    beautifulsoup4==4.14.3
    boolean.py==5.0
    boto3==1.43.97
    botocore==1.43.97
    build==1.6.1
    CacheControl==0.14.4
    cachetools==7.2.0
    certifi==2026.2.25
    cffi==2.0.0
    cfgv==3.5.0
    charset-normalizer==3.4.6
    check-wheel-contents==0.6.3
    ciu==7.14.0
    click==8.5.0
    cmru==5.3.0
    colorama==0.4.6
    ConfigArgParse==1.7.5
    coverage==7.16.1
    cryptography==50.0.1
    cyclonedx-python-lib==11.12.0
    debugpy==1.8.22
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
    greenlet==3.5.6
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
    ipython==9.17.1
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
    librt==0.15.0
    license-expression==30.4.4
    linkify-it-py==2.2.0
    litellm==1.82.3
    markdown-it-py==4.0.0
    MarkupSafe==3.0.3
    matplotlib-inline==0.2.2
    mccabe==0.7.0
    mdit-py-plugins==0.6.1
    mdurl==0.1.2
    mixpanel==5.0.0
    more-itertools==11.1.0
    msgpack==1.2.2
    mslex==1.3.0
    multidict==6.7.1
    mypy==2.3.1
    mypy_extensions==1.1.0
    networkx==3.4.2
    nh3==0.3.7
    nodeenv==1.10.0
    numpy==2.4.6
    nyxloom==0.7.0
    openai==2.28.0
    orjson==3.11.7
    oslex==0.1.3
    packageurl-python==0.17.6
    packaging==26.3
    parso==0.8.7
    pathspec==1.0.4
    pexpect==4.9.0
    pillow==12.1.1
    pip==26.2.1
    pip_api==0.0.35
    pip_audit==2.10.1
    pip-requirements-parser==32.0.1
    platformdirs==4.11.10
    pluggy==1.6.0
    posthog==7.9.12
    pre_commit==4.6.2
    prompt_toolkit==3.0.52
    propcache==0.4.1
    psutil==7.2.2
    ptyprocess==0.7.0
    pure_eval==0.2.4
    py-serializable==2.1.0
    pycodestyle==2.14.0
    pycparser==3.0
    pydantic==2.13.5
    pydantic_core==2.46.5
    pydantic-settings==2.15.0
    pydub==0.25.1
    pyflakes==3.4.0
    Pygments==2.21.0
    pypandoc==1.17
    pyparsing==3.3.2
    pyperclip==1.11.0
    pyproject-api==1.11.2
    pyproject_hooks==1.3.3
    pytest==9.1.1
    pytest-asyncio==1.4.0
    pytest-cov==7.1.0
    pytest-mock==3.15.1
    pytest-xdist==3.8.0
    python-dateutil==2.9.0.post0
    python-discovery==1.6.1
    python-dotenv==1.2.2
    PyYAML==6.0.3
    readme_renderer==46.0
    redis==8.1.0
    referencing==0.37.0
    regex==2026.2.28
    requests==2.34.2
    requests-toolbelt==1.0.0
    rfc3986==2.0.0
    rich==15.0.0
    rpds-py==0.30.0
    ruff==0.16.8
    s3transfer==0.19.2
    scipy==1.17.1
    SecretStorage==3.5.0
    setuptools==84.0.0
    setuptools-scm==10.2.3
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
    SQLAlchemy==2.0.54
    stack-data==0.6.3
    starlette==0.52.1
    structlog==26.1.0
    textual==8.2.8
    tiktoken==0.12.0
    tokenizers==0.22.2
    tomli==2.4.1
    tomli_w==1.2.0
    topos==0.3.2
    tox==4.61.5
    tqdm==4.67.3
    traitlets==5.16.1
    tree-sitter==0.25.2
    tree-sitter-c-sharp==0.23.1
    tree-sitter-embedded-template==0.25.0
    tree-sitter-language-pack==0.13.0
    tree-sitter-yaml==0.7.2
    twine==7.0.0
    typer==0.24.1
    typing_extensions==4.15.0
    typing-inspection==0.4.2
    urllib3==2.6.3
    uv==0.12.16
    vcs-versioning==2.4.1
    virtualenv==21.7.12
    watchfiles==1.1.1
    wcwidth==0.6.0
    websockets==17.1
    wheel==0.48.0
    wheel-filename==1.4.2
    yarl==1.23.0
    zipp==3.23.0

## System Packages (source: Debian trixie apt)

    (installed via apt)
**Version policy:** Debian repository versions (prefer backports when available). System packages come from the Debian Trixie main repos; devcontainer-features are installed via the features CLI.  Exception: `skopeo` is pulled from Debian testing (pin-priority 501) for a newer version than trixie provides.

- bash-completion: `1:2.16.0-7`
- bind9-dnsutils: `1:9.20.29-1~deb13u1`
- ca-certificates: `20250419`
- composer: `2.8.8-1+deb13u3`
- curl: `8.14.1-2+deb13u5`
- fuse3: `3.17.2-3`
- gdb: `16.3-1`
- git: `1:2.47.3-0+deb13u1`
- git-lfs: `3.6.1-1+deb13u1`
- gnupg: `2.4.7-21+deb13u1`
- gzip: `1.13-1+deb13u1`
- httpie: `3.2.4-3`
- iputils-ping: `3:20240905-3`
- jq: `1.7.1-6+deb13u3`
- less: `668-1`
- locales: `2.41-12+deb13u4`
- lsb-release: `12.1-1`
- lsof: `4.99.4+dfsg-2`
- man-db: `2.13.1-1`
- mc: `3:4.8.33-1+deb13u1`
- nano: `8.4-1+deb13u1`
- ncdu: `1.22-1`
- netcat-openbsd: `1.229-1`
- openssl: `3.5.7-1~deb13u2`
- php8.5-cli: `8.5.10-1+0~20260828.25+debian13~1.gbpfea0b8`
- php8.5-curl: `8.5.10-1+0~20260828.25+debian13~1.gbpfea0b8`
- php8.5-fpm: `8.5.10-1+0~20260828.25+debian13~1.gbpfea0b8`
- php8.5-gd: `8.5.10-1+0~20260828.25+debian13~1.gbpfea0b8`
- php8.5-intl: `8.5.10-1+0~20260828.25+debian13~1.gbpfea0b8`
- php8.5-mbstring: `8.5.10-1+0~20260828.25+debian13~1.gbpfea0b8`
- php8.5-xdebug: `3.5.3-1+0~20260620.69+debian13~1.gbp8ad7a9`
- php8.5-xml: `8.5.10-1+0~20260828.25+debian13~1.gbpfea0b8`
- php8.5-zip: `8.5.10-1+0~20260828.25+debian13~1.gbpfea0b8`
- postgresql-client: `18+293.pgdg13+1`
- psmisc: `23.7-2`
- python3-venv: `3.13.5-1`
- redis-tools: `6:8.10.2-1rl1~trixie1`
- rsync: `3.4.1+ds1-5+deb13u4`
- skopeo: `1.22.0+ds1-1`
- sqlite3: `3.46.1-7+deb13u2`
- sshfs: `3.7.3-1.2~deb13u1`
- strace: `6.13+ds-1`
- tar: `1.35+dfsg-3.1`
- tree: `2.2.1-1`
- unzip: `6.0-29+deb13u1`
- vim: `2:9.1.1230-2`
- w3m: `0.5.3+git20230121-2.1`
- wget: `1.25.0-2`
- xz-utils: `5.8.1-1+deb13u1`

## Rich Documentation Links

- Family overview: https://github.com/volkb79-2/vbpub/blob/main/modern-debian-tools-python-debug/package-manifests-versioned/modern-debian-tools-python-debug/README.md
- This release page: https://github.com/volkb79-2/vbpub/blob/main/modern-debian-tools-python-debug/package-manifests-versioned/modern-debian-tools-python-debug/trixie-py3.14-php8.5-20260918.md
- Source tree: https://github.com/volkb79-2/vbpub/tree/main/modern-debian-tools-python-debug

## In-Image File

- Image manifest: `/usr/local/share/modern-debian-tools-python-debug/manifest.md`

## Notes

This repository-hosted page exists because GHCR package descriptions render as flattened plain text.
The image labels therefore point to GitHub-hosted Markdown for richer, package-specific release notes.
The same manifest content is installed in-image at `/usr/local/share/modern-debian-tools-python-debug/manifest.md`.
