# Image Manifest — trixie-py3.14-php8.5-20261022

## Release

- Build date: `20261022`
- Built at (UTC): `2026-10-22T11:57:34Z`
- Debian: `trixie`
- Python: `3.14`
- Immutable image tag: `trixie-py3.14-php8.5-20261022`
- Floating image tag: `trixie-py3.14-php8.5-latest`

## Pull

```bash
docker pull ghcr.io/volkb79-2/mdt-cockpit:trixie-py3.14-php8.5-20261022
```

## Base

- Debian: trixie
- Python: 3.14
- Image version: 20261022
- Image tag: trixie-py3.14-php8.5-20261022

## Purpose

mdt-cockpit + PHP 8.5 base image. Adds PHP 8.5 CLI/FPM, composer, Xdebug, and the common PHP extensions needed for web debugging. Docs: https://github.com/volkb79-2/vbpub/blob/main/mdt-cockpit/package-manifests-versioned/mdt-cockpit/trixie-py3.14-php8.5-20261022.md

## Node Runtime (source: nodesource apt repo (https://deb.nodesource.com))

- Node.js: `v26.12.1`
- npm: `11.20.0`

## Python & PHP Runtime (source: Debian trixie apt / sury.org PHP repo (https://packages.sury.org/php))

- PHP: `PHP 8.5.12 (cli) (built: Oct 21 2026 08:09:42) (NTS)`
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
| `CIU` | `7.17.0` | latest |  |  |
| `cmru` | `6.2.1` | latest |  |  |

## AI CLI Tools (source: npm / PyPI / GitHub Releases)

**Version policy:** claude, codex, reasonix, opencode and pi follow upstream latest and the image records the exact version it shipped; copilot, openclaw and claudelink are the newest release at least 14 days old recorded by `cmru versions`, installed with every dependency held to the same cutoff; aider is an exact upstream commit (documented exception). Versions are staged by `stage_tool_artifacts` into tool-versions.env.

| Tool | Version | Policy | Project Home | Package digest / source |
|---|---|---|---|---|
| `aider` | `aider 0.86.3.dev53+g5dc9490bb` | latest | https://github.com/Aider-AI/aider |  |
| `antigravity` | `1.4.5` | latest | https://github.com/antigravity/antigravity-cli | [`sha256:90cc1894b6862510869ad4cf…`](https://storage.googleapis.com/antigravity-public/antigravity-cli/1.4.5-4952523841339392/linux-x64/cli_linux_x64.tar.gz) |
| `claude` | `2.1.308 (Claude Code)` | latest | https://github.com/anthropics/claude-code |  |
| `claudelink` | `1.6.1` | latest | https://github.com/RBJGlobal/claudelink |  |
| `codex` | `codex-cli 0.163.0` | latest | https://github.com/openai/codex |  |
| `copilot` | `Package extraction took 17883ms` | latest | https://github.com/github/copilot-cli |  |
| `openclaw` | `OpenClaw 2026.9.8 (fc23bc8)` | latest | https://github.com/openclaw/openclaw |  |
| `opencode` | `1.19.3` | latest | https://github.com/anomalyco/opencode |  |
| `pi` | `1.4.0` | latest | https://github.com/earendil-works/pi |  |
| `reasonix` | `reasonix v2.48.0` | latest | https://github.com/reasonix/reasonix |  |

## Container Inspection Tools (source: GitHub Releases (pre-built binaries))

**Version policy:** latest GitHub release at build time (override via build arg). All tools in this category are downloaded as pre-built binaries from their upstream releases.

| Tool | Version | Policy | Project Home | Package digest / source |
|---|---|---|---|---|
| `crane` | `0.22.1` | latest | https://github.com/google/go-containerregistry | [`sha256:0ab7a1d6932a213aed964ce9…`](https://github.com/google/go-containerregistry/releases/download/v0.22.1/go-containerregistry_Linux_x86_64.tar.gz) |
| `dive` | `dive 0.13.1` | latest | https://github.com/wagoodman/dive | [`sha256:0c20d18f0cc87e6e982a3289…`](https://github.com/wagoodman/dive/releases/download/v0.13.1/dive_0.13.1_linux_amd64.deb) |
| `dtop` | `dtop 0.10.4` | latest | https://github.com/amir20/dtop | [`sha256:b4c562b0d12a0d40b87856ac…`](https://github.com/amir20/dtop/releases/download/v0.10.4/dtop-x86_64-unknown-linux-gnu.tar.gz) |
| `glances` | `Glances version:	4.5.8` | latest | https://github.com/nicolargo/glances | [`sha256:12a118a17c970235f3f218f7…`](https://pypi.org/pypi/glances/4.5.8/json) |
| `lazydocker` | `Version: 0.25.2` | latest | https://github.com/jesseduffield/lazydocker | [`sha256:0d9dbfc26068b218e7ed84b1…`](https://github.com/jesseduffield/lazydocker/releases/download/v0.25.2/lazydocker_0.25.2_Linux_x86_64.tar.gz) |
| `lnav` | `0.14.1` | latest | https://github.com/tstack/lnav | [`sha256:ec1750f0a6962eed6bb85ad4…`](https://github.com/tstack/lnav/releases/download/v0.14.1/lnav-0.14.1-linux-musl-x86_64.zip) |
| `regctl` | `0.11.7` | latest | https://github.com/regclient/regclient | [`sha256:3737fbbdd791e04e7a831640…`](https://github.com/regclient/regclient/releases/download/v0.11.7/regctl-linux-amd64) |
| `syft` | `syft 1.55.0` | latest | https://github.com/anchore/syft | [`sha256:bb65249542db5fedeeb4d3f9…`](https://github.com/anchore/syft/releases/download/v1.55.0/syft_1.55.0_linux_amd64.tar.gz) |

## Security & Debug Tools (source: GitHub Releases (pre-built binaries, sha256-verified))

**Version policy:** latest GitHub release at build time (override via build arg). Binaries are verified via upstream SHA256 checksums before installation.

| Tool | Version | Policy | Project Home | Package digest / source |
|---|---|---|---|---|
| `cdebug` | `cdebug version 0.0.19 (built: 2026-01-18T19:11:40Z commit: 6c205f0b663df4dec235f42e905e94b40709159a)` | latest | https://github.com/iximiuz/cdebug | [`sha256:10c2dd283ed690f445ac41d7…`](https://github.com/iximiuz/cdebug/releases/download/v0.0.19/cdebug_linux_amd64.tar.gz) |
| `grype` | `Application:         grype` | latest | https://github.com/anchore/grype | [`sha256:64e07133d3486115de647a66…`](https://github.com/anchore/grype/releases/download/v0.121.0/grype_0.121.0_linux_amd64.tar.gz) |
| `hadolint` | `Haskell Dockerfile Linter 2.15.1` | latest | https://github.com/hadolint/hadolint | [`sha256:c7187db94eeeeca956519a6a…`](https://github.com/hadolint/hadolint/releases/download/v2.15.1/hadolint-linux-x86_64) |

## Custom Tooling (source: GitHub Releases / Debian apt)

**Version policy:** latest GitHub release at build time (override via build arg). Some tools are compiled from source (nvim, htop); the rest are pre-built binaries.

| Tool | Version | Policy | Project Home | Package digest / source |
|---|---|---|---|---|
| `awscli` | `aws-cli/2.36.9 Python/3.14.6 Linux/7.2.9+deb13-amd64 exe/x86_64.debian.13` | latest | https://github.com/aws/aws-cli | [`sha256:9b92ccb50dfc55479ac14c4b…`](https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip) |
| `b2` | `b2 command line tool, version 5.0.1 (b2sdk version 2.13.2)` | latest | https://github.com/Backblaze/B2_Command_Line_Tool | [`sha256:01a846522591e1ef90ad600e…`](https://github.com/Backblaze/B2_Command_Line_Tool/releases/download/v5.0.1/b2-linux) |
| `bat` | `bat 0.26.1 (979ba22)` | latest | https://github.com/sharkdp/bat | [`sha256:726f04c8f576a7fd18b7634f…`](https://github.com/sharkdp/bat/releases/download/v0.26.1/bat-v0.26.1-x86_64-unknown-linux-gnu.tar.gz) |
| `composer` | `Composer version 2.8.8 2025-04-04 16:56:46` | latest |  |  |
| `consul` | `Consul v2.0.4` | latest | https://github.com/hashicorp/consul | [`sha256:7a28033850a24fd411722593…`](https://releases.hashicorp.com/consul/2.0.4/consul_2.0.4_linux_amd64.zip) |
| `delta` | `delta 0.20.1` | latest | https://github.com/dandavison/delta | [`sha256:50f08c879f84c81ceb220e47…`](https://github.com/dandavison/delta/releases/download/0.20.1/delta-0.20.1-x86_64-unknown-linux-gnu.tar.gz) |
| `dust` | `1.2.6` | latest | https://github.com/bootandy/dust | [`sha256:cbf2cb5c9d6227e46fc4b29c…`](https://github.com/bootandy/dust/releases/download/v1.2.6/dust-v1.2.6-x86_64-unknown-linux-musl.tar.gz) |
| `fd` | `fd 10.5.0` | latest | https://github.com/sharkdp/fd | [`sha256:a1259cd129636efbc3fef123…`](https://github.com/sharkdp/fd/releases/download/v10.5.0/fd-v10.5.0-x86_64-unknown-linux-gnu.tar.gz) |
| `fzf` | `0.74.5 (c0a193e0)` | latest | https://github.com/junegunn/fzf | [`sha256:d47d7f882972c7d674dbbba9…`](https://github.com/junegunn/fzf/releases/download/v0.74.5/fzf-0.74.5-linux_amd64.tar.gz) |
| `gdu` | `5.38.0` | latest | https://github.com/dundee/gdu | [`sha256:e7b33d9a04597ac2caa2d7ff…`](https://github.com/dundee/gdu/releases/download/v5.38.0/gdu_linux_amd64_static.tgz) |
| `gh` | `gh version 2.103.0 (2026-10-21)` | latest | https://github.com/cli/cli | [`sha256:fdd768eb70f6409f80fcfa9f…`](https://github.com/cli/cli/releases/download/v2.103.0/gh_2.103.0_linux_amd64.tar.gz) |
| `grpcurl` | `grpcurl v1.9.4` | latest | https://github.com/fullstorydev/grpcurl | [`sha256:97e13d58d2733a0e62cd2571…`](https://github.com/fullstorydev/grpcurl/releases/download/v1.9.4/grpcurl_1.9.4_linux_x86_64.tar.gz) |
| `htop` | `htop 3.5.3` | latest | https://github.com/htop-dev/htop | [`sha256:edf25ee020a5263ffbef9eef…`](https://github.com/htop-dev/htop/archive/refs/tags/3.5.3.tar.gz) |
| `ksm-optin` | `true` | latest |  |  |
| `nvchad` | `2.5` | latest | https://github.com/NvChad/NvChad | [`sha256:738b167881a1a08880442040…`](https://github.com/NvChad/NvChad/archive/refs/tags/v2.5.tar.gz) |
| `nvim` | `0.12.6` | latest | https://github.com/neovim/neovim | [`sha256:474430d53e6264f6d6dd18db…`](https://github.com/neovim/neovim/releases/download/v0.12.6/nvim-linux-x86_64.tar.gz) |
| `php` | `PHP 8.5.12 (cli) (built: Oct 21 2026 08:09:42) (NTS)` | latest |  |  |
| `rga` | `ripgrep-all 0.10.10` | latest | https://github.com/phiresky/ripgrep-all | [`sha256:a969c25b182ac84aa6725183…`](https://github.com/phiresky/ripgrep-all/releases/download/v0.10.10/ripgrep_all-v0.10.10-x86_64-unknown-linux-musl.tar.gz) |
| `ripgrep` | `ripgrep 15.2.0 (rev e89fff89ac)` | latest | https://github.com/BurntSushi/ripgrep | [`sha256:33e15bcf1624b25cdd2a5581…`](https://github.com/BurntSushi/ripgrep/releases/download/15.2.0/ripgrep-15.2.0-x86_64-unknown-linux-musl.tar.gz) |
| `shellcheck` | `0.11.0` | latest | https://github.com/koalaman/shellcheck | [`sha256:8c3be12b05d5c177a04c29e3…`](https://github.com/koalaman/shellcheck/releases/download/v0.11.0/shellcheck-v0.11.0.linux.x86_64.tar.xz) |
| `vault` | `Vault v2.2.0 (2891bfd23893ebd302307050edaf03d871ea8351), built 2026-10-14T15:24:08Z` | latest | https://github.com/hashicorp/vault | [`sha256:05d78b0ac842ca7c522fc297…`](https://releases.hashicorp.com/vault/2.2.0/vault_2.2.0_linux_amd64.zip) |
| `yq` | `yq (https://github.com/mikefarah/yq/) version v4.54.1` | latest | https://github.com/mikefarah/yq | [`sha256:8e34fc298390875de416e6a4…`](https://github.com/mikefarah/yq/releases/download/v4.54.1/yq_linux_amd64) |

## Python Packages (source: PyPI (resolved at build time via pip))

    (installed via pip)
**Version policy:** PyPI latest at image build time (resolved via pip install). The primary venv contains full toolkit.txt closure; secondary venvs are lean (uv + debugpy + ruff only).

    aider-chat==0.86.3.dev53+g5dc9490bb
    aiohappyeyeballs==2.6.1
    aiohttp==3.14.5
    aiosignal==1.4.0
    annotated-doc==0.0.4
    annotated-types==0.7.0
    anyio==4.12.1
    asgiref==3.11.1
    ast_serialize==0.13.1
    asttokens==3.0.2
    asyncpg==0.32.0
    attrs==25.4.0
    audioop-lts==0.2.2
    backoff==2.2.1
    beautifulsoup4==4.14.3
    boolean.py==5.0
    boto3==1.43.117
    botocore==1.43.117
    build==1.6.1
    CacheControl==0.14.4
    cachetools==7.2.4
    certifi==2026.2.25
    cffi==2.0.0
    cfgv==3.5.0
    charset-normalizer==3.4.6
    check-wheel-contents==0.6.3
    ciu==7.17.0
    cli-extended==0.5.0
    click==8.5.0
    cmru==6.2.1
    colorama==0.4.6
    ConfigArgParse==1.7.5
    coverage==7.16.2
    cryptography==50.0.2
    cyclonedx-python-lib==11.12.0
    debugpy==1.8.22
    decorator==5.3.1
    defusedxml==0.7.1
    diff-match-patch==20241021
    diskcache==5.6.3
    distlib==0.4.4
    distro==1.9.0
    dnspython==2.9.0
    docutils==0.23
    execnet==2.1.2
    executing==2.3.0
    fastapi==0.135.1
    fastuuid==0.14.0
    filelock==3.25.2
    flake8==7.3.0
    frozenlist==1.8.0
    fsspec==2026.2.0
    gitdb==4.0.12
    GitPython==3.1.46
    grep-ast==0.9.0
    h11==0.16.0
    hf-xet==1.4.2
    httpcore==1.0.9
    httpx==0.28.1
    huggingface_hub==1.7.1
    hvac==2.4.0
    id==1.6.1
    identify==2.6.20
    idna==3.11
    importlib_metadata==7.2.1
    importlib_resources==6.5.2
    iniconfig==2.3.1
    ipdb==0.13.13
    ipython==9.17.1
    ipython_pygments_lexers==1.1.1
    jaraco.classes==3.4.0
    jaraco.context==6.1.2
    jaraco.functools==4.6.0
    jedi==0.20.1
    jeepney==0.9.0
    Jinja2==3.1.6
    jiter==0.13.0
    jmespath==1.1.0
    json5==0.13.0
    jsonschema==4.26.0
    jsonschema-specifications==2025.9.1
    keyring==25.7.0
    librt==0.16.0
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
    more-itertools==11.2.1
    msgpack==1.2.3
    mslex==1.3.0
    multidict==6.7.1
    mypy==2.4.0
    mypy_extensions==1.1.0
    networkx==3.4.2
    nh3==0.3.7
    nodeenv==1.11.0
    numpy==2.4.6
    nyxloom==0.11.0
    openai==2.28.0
    orjson==3.11.7
    oslex==0.1.3
    packageurl-python==0.17.6
    packaging==26.3
    parso==0.8.7
    pathspec==1.0.4
    pexpect==4.9.0
    pillow==12.1.1
    pip==26.2.2
    pip_api==0.0.35
    pip_audit==2.10.1
    pip-requirements-parser==32.0.1
    platformdirs==4.13.5
    pluggy==1.7.0
    posthog==7.9.12
    pre_commit==4.7.0
    prompt_toolkit==3.0.52
    propcache==0.4.1
    psutil==7.2.2
    ptyprocess==0.7.0
    pure_eval==0.2.4
    py-serializable==2.1.0
    pycodestyle==2.14.0
    pycparser==3.0
    pydantic==2.14.1
    pydantic_core==2.50.1
    pydantic-settings==2.16.1
    pydub==0.25.1
    pyflakes==3.4.0
    Pygments==2.21.0
    pypandoc==1.17
    pyparsing==3.3.3
    pyperclip==1.11.0
    pyproject-api==1.12.0
    pyproject_hooks==1.3.3
    pytest==9.1.1
    pytest-asyncio==1.4.0
    pytest-cov==7.1.0
    pytest-mock==3.17.0
    pytest-xdist==3.8.0
    python-dateutil==2.9.0.post0
    python-discovery==1.6.4
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
    ruff==0.17.1
    s3transfer==0.19.3
    scipy==1.17.1
    SecretStorage==3.5.0
    setuptools==84.2.1
    setuptools-scm==10.3.4
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
    SQLAlchemy==2.1.6
    stack-data==0.6.3
    starlette==0.52.1
    structlog==26.1.0
    textual==8.2.8
    tiktoken==0.12.0
    tokenizers==0.22.2
    tomli==2.5.0
    tomli_w==1.2.0
    topos==0.4.2
    tox==4.67.1
    tqdm==4.67.3
    traitlets==5.16.1
    tree-sitter==0.25.2
    tree-sitter-c-sharp==0.23.1
    tree-sitter-embedded-template==0.25.0
    tree-sitter-language-pack==0.13.0
    tree-sitter-yaml==0.7.2
    twine==7.0.0
    typer==0.24.1
    typing_extensions==4.16.0
    typing-inspection==0.4.4
    urllib3==2.6.3
    uv==0.13.5
    vcs-versioning==2.6.0
    virtualenv==21.14.12
    watchfiles==1.1.1
    wcwidth==0.6.0
    websockets==17.3
    wheel==0.48.0
    wheel-filename==1.4.2
    yarl==1.25.1
    zipp==3.23.0

## System Packages (source: Debian trixie apt)

    (installed via apt)
**Version policy:** Debian repository versions (prefer backports when available). System packages come from the Debian Trixie main repos; devcontainer-features are installed via the features CLI.  Exception: `skopeo` is pulled from Debian testing (pin-priority 701) for a newer version than trixie provides.

- bash-completion: `1:2.16.0-7`
- bind9-dnsutils: `1:9.20.30-1~deb13u1`
- ca-certificates: `20250419+deb13u1`
- composer: `2.8.8-1+deb13u3`
- curl: `8.21.0-2~bpo13+1`
- fuse3: `3.17.2-3`
- gdb: `18.1-3~bpo13+1`
- git: `1:2.47.3-0+deb13u1`
- git-lfs: `3.6.1-1+deb13u1`
- gnupg: `2.4.7-21+deb13u1`
- gzip: `1.13-1+deb13u1`
- httpie: `3.2.4-3`
- iputils-ping: `3:20240905-3`
- jq: `1.7.1-6+deb13u4`
- less: `668-1`
- locales: `2.41-12+deb13u4`
- lsb-release: `12.1-1`
- lsof: `4.99.4+dfsg-2`
- man-db: `2.13.1-1`
- mc: `3:4.8.33-1+deb13u1`
- nano: `8.4-1+deb13u1`
- ncdu: `1.22-1`
- netcat-openbsd: `1.229-1`
- openssl: `3.5.7-1~deb13u3`
- php8.5-cli: `8.5.12-1+0~20261021.27+debian13~1.gbp1a3e8a`
- php8.5-curl: `8.5.12-1+0~20261021.27+debian13~1.gbp1a3e8a`
- php8.5-fpm: `8.5.12-1+0~20261021.27+debian13~1.gbp1a3e8a`
- php8.5-gd: `8.5.12-1+0~20261021.27+debian13~1.gbp1a3e8a`
- php8.5-intl: `8.5.12-1+0~20261021.27+debian13~1.gbp1a3e8a`
- php8.5-mbstring: `8.5.12-1+0~20261021.27+debian13~1.gbp1a3e8a`
- php8.5-xdebug: `3.5.3-1+0~20260620.69+debian13~1.gbp8ad7a9`
- php8.5-xml: `8.5.12-1+0~20261021.27+debian13~1.gbp1a3e8a`
- php8.5-zip: `8.5.12-1+0~20261021.27+debian13~1.gbp1a3e8a`
- postgresql-client: `18+293.pgdg13+1`
- psmisc: `23.7-2`
- python3-venv: `3.13.5-1`
- redis-tools: `6:8.10.2-1rl1~trixie1`
- rsync: `3.5.0+ds1-0+deb13u1`
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
- xz-utils: `5.8.1-1+deb13u2`

## Rich Documentation Links

- Family overview: https://github.com/volkb79-2/vbpub/blob/main/mdt-cockpit/package-manifests-versioned/mdt-cockpit/README.md
- This release page: https://github.com/volkb79-2/vbpub/blob/main/mdt-cockpit/package-manifests-versioned/mdt-cockpit/trixie-py3.14-php8.5-20261022.md
- Source tree: https://github.com/volkb79-2/vbpub/tree/main/mdt-cockpit

## In-Image File

- Image manifest: `/usr/local/share/mdt-cockpit/manifest.md`

## Notes

This repository-hosted page exists because GHCR package descriptions render as flattened plain text.
The image labels therefore point to GitHub-hosted Markdown for richer, package-specific release notes.
The same manifest content is installed in-image at `/usr/local/share/mdt-cockpit/manifest.md`.
