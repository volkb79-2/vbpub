#!/usr/bin/env python3
"""
SSH Key Generation and Deployment Tool
======================================

Unified tool for generating SSH keys and deploying them locally or remotely.

Usage:
    # Generate key and install locally (server mode)
    ./ssh-keygen-deploy.py --user root --send-private

    # Generate key and deploy to remote server (client mode)
    ./ssh-keygen-deploy.py --remote user@server.example.com --key-owner myname --key-hostname laptop

    # Bootstrap integration (non-interactive)
    ./ssh-keygen-deploy.py --user root --send-private --non-interactive

Environment Variables:
    TELEGRAM_BOT_TOKEN - Telegram bot token for notifications
    TELEGRAM_CHAT_ID - Telegram chat ID for notifications
    NONINTERACTIVE - Skip interactive prompts (yes/no)


## Naming Conventions

### Private Key Filename

**Format:** `<service>_<user>_<algorithm>`

**Purpose:** Identifies **WHICH** server/service to connect **TO**

**Examples:**
- `netcup-hosting218629-ed25519`
- `github-vb-ed25519`
- `aws-ec2-production-rsa`

**Why:** The private key filename should immediately tell you what it's used for - which service or server you're connecting to.

### Public Key Comment

**Format:** `<owner>@<hostname_or_context>_<date_or_version>`

**Purpose:** Identifies **WHO** is using the key (appears in server's `authorized_keys`)

**Examples:**
- `vb@gstammtisch.dchive.de_202511`
- `john@devlaptop_202601`
- `ci-bot@github-actions_v2`

**Why:** This comment appears in the server's `authorized_keys` file, helping administrators identify which key belongs to which developer, machine, or system.


### SSH Config

Create `~/.ssh/config` entries for convenience:

```
Host netcup-prod
    HostName hosting218629.ae98d.netcup.net
    User hosting218629
    IdentityFile ~/.ssh/netcup-hosting218629-ed25519
    
Host github
    HostName github.com
    User git
    IdentityFile ~/.ssh/github-vb-ed25519
```

## USAGE 

**New:** on local system (server mode), send private key via Telegram
```bash
TELEGRAM_BOT_TOKEN=123:abc \
TELEGRAM_CHAT_ID=456 \
./ssh-keygen-deploy.py --user root --send-private --non-interactive
```
**New:** on remote system (client mode), deploy public key via ssh-copy-id
```bash
./ssh-keygen-deploy.py \
  --remote hosting218629@hosting218629.ae98d.netcup.net \
  --key-owner vb \
  --key-hostname gstammtisch.dchive.de \
  --service netcup
```

### Pattern 1: Bootstrap Server Setup

```bash
# Complete bootstrap with SSH key
curl -fsSL https://raw.githubusercontent.com/volkb79/vbpub/main/scripts/debian-install/bootstrap.sh | \
TELEGRAM_BOT_TOKEN=<token> \
TELEGRAM_CHAT_ID=<chat-id> \
SETUP_SSH_ACCESS=yes \
SSH_ACCESS_USER=root \
bash
```

### Pattern 2: Manual Server Setup

```bash
# Clone repo
git clone https://github.com/volkb79/vbpub.git /opt/vbpub

# Generate and send key
export TELEGRAM_BOT_TOKEN="<token>"
export TELEGRAM_CHAT_ID="<chat-id>"

python3 /opt/vbpub/scripts/ssh-keygen-deploy.py \
  --user root \
  --send-private \
  --non-interactive
```

### Pattern 3: Client Access Setup

```bash
# For accessing Netcup hosting
./ssh-keygen-deploy.py \
  --remote hosting218629@hosting218629.ae98d.netcup.net \
  --key-owner vb \
  --key-hostname workstation \
  --service netcup

# For accessing GitHub
./ssh-keygen-deploy.py \
  --remote git@github.com \
  --key-owner vb \
  --key-hostname laptop \
  --service github
```

### Pattern 4: Automated CI/CD

```bash
# No passphrase, deploy to production
./ssh-keygen-deploy.py \
  --remote deploy@prod.example.com \
  --key-owner ci-bot \
  --key-hostname github-actions \
  --service production \
  --non-interactive
```

"""

import argparse
import os
import sys
import subprocess
import socket
import logging
import pwd
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple

# Configure logging
logging.basicConfig(
    format='[%(levelname)s] %(asctime)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


class SSHKeyManager:
    """Manage SSH key generation and deployment"""

    def __init__(
        self,
        user: str,
        algorithm: str = 'ed25519',
        key_storage_path: Optional[str] = None,
        telegram_bot_token: Optional[str] = None,
        telegram_chat_id: Optional[str] = None,
        non_interactive: bool = False
    ):
        self.user = user
        self.algorithm = algorithm

        if key_storage_path:
            self.key_storage_path = Path(key_storage_path)
        else:
            try:
                home_dir = pwd.getpwnam(user).pw_dir
            except KeyError:
                home_dir = f'/home/{user}'
            self.key_storage_path = Path(home_dir) / '.ssh'

        self.telegram_bot_token = telegram_bot_token or os.environ.get('TELEGRAM_BOT_TOKEN')
        self.telegram_chat_id = telegram_chat_id or os.environ.get('TELEGRAM_CHAT_ID')
        self.non_interactive = non_interactive or os.environ.get('NONINTERACTIVE', '').lower() == 'yes'
        self.version = datetime.now().strftime('%Y%m')

        # Find telegram client
        self.telegram_client = self._find_telegram_client()

    def _find_telegram_client(self) -> Optional[Path]:
        """Find telegram_client.py script (moved to scripts/telegram/ - has its own tests/run-gate now)"""
        possible_paths = [
            Path('/opt/vbpub/scripts/telegram/telegram_client.py'),
            Path(__file__).parent / 'telegram' / 'telegram_client.py',
            Path(__file__).parent.parent / 'scripts' / 'telegram' / 'telegram_client.py'
        ]
        
        for path in possible_paths:
            if path.exists():
                return path
        return None

    def _run_command(self, cmd: list, check: bool = True, capture_output: bool = True) -> subprocess.CompletedProcess:
        """Run shell command with error handling"""
        try:
            result = subprocess.run(
                cmd,
                check=check,
                capture_output=capture_output,
                text=True,
                timeout=30
            )
            return result
        except subprocess.CalledProcessError as e:
            logger.error(f"Command failed: {' '.join(cmd)}")
            logger.error(f"Error: {e.stderr}")
            raise
        except subprocess.TimeoutExpired:
            logger.error(f"Command timed out: {' '.join(cmd)}")
            raise

    def _get_system_hostname(self) -> Tuple[str, str]:
        """Get system hostname (FQDN and short)"""
        try:
            fqdn = socket.getfqdn()
            short = socket.gethostname()
            if fqdn in ('localhost', 'localhost.localdomain', ''):
                fqdn = short
            return fqdn, short
        except Exception as e:
            logger.warning(f"Failed to get hostname: {e}")
            return 'unknown', 'unknown'

    def _get_primary_ip(self) -> str:
        """Get primary IP address"""
        try:
            result = self._run_command(['hostname', '-I'])
            ips = result.stdout.strip().split()
            return ips[0] if ips else 'unknown'
        except Exception:
            return 'unknown'

    def _send_telegram_message(self, message: str) -> bool:
        """Send message via Telegram"""
        if not self.telegram_bot_token or not self.telegram_chat_id:
            return False
        if not self.telegram_client:
            logger.warning("Telegram client not found")
            return False

        try:
            result = self._run_command([
                'python3',
                str(self.telegram_client),
                '--send',
                message
            ], check=False)
            return result.returncode == 0
        except Exception as e:
            logger.warning(f"Failed to send Telegram message: {e}")
            return False

    def _send_telegram_file(self, file_path: Path, caption: str = '') -> bool:
        """Send file via Telegram"""
        if not self.telegram_bot_token or not self.telegram_chat_id:
            return False
        if not self.telegram_client:
            logger.warning("Telegram client not found")
            return False
        if not file_path.exists():
            logger.warning(f"File not found: {file_path}")
            return False

        try:
            cmd = [
                'python3',
                str(self.telegram_client),
                '--file',
                str(file_path)
            ]
            if caption:
                cmd.extend(['--caption', caption])
            
            result = self._run_command(cmd, check=False)
            return result.returncode == 0
        except Exception as e:
            logger.warning(f"Failed to send Telegram file: {e}")
            return False

    def _get_key_fingerprint(self, public_key_path: Path, hash_type: str = 'sha256') -> str:
        """Get SSH key fingerprint"""
        try:
            result = self._run_command([
                'ssh-keygen',
                '-lf',
                str(public_key_path),
                '-E',
                hash_type
            ])
            # Parse output: "2048 SHA256:xxx... comment (RSA)"
            parts = result.stdout.strip().split()
            return parts[1] if len(parts) > 1 else 'unknown'
        except Exception as e:
            logger.warning(f"Failed to get fingerprint: {e}")
            return 'unknown'

    def generate_key_local(
        self,
        key_owner: str,
        key_hostname: str,
        send_private: bool = False
    ) -> Tuple[Path, Path]:
        """
        Generate SSH key for local installation (server mode)
        
        Args:
            key_owner: Owner identifier (e.g., 'client', 'admin')
            key_hostname: Hostname/context for the key
            send_private: Whether to send private key via Telegram
            
        Returns:
            Tuple of (private_key_path, public_key_path)
        """
        hostname_fqdn, hostname_short = self._get_system_hostname()
        
        # Naming convention:
        # - Private key filename: server-access-<hostname>-<algorithm>
        # - Public key comment: <owner>@<hostname>_<version>
        
        key_comment = f"{key_owner}@{hostname_fqdn}_{self.version}"
        key_filename = f"server-access-{hostname_short}-{self.algorithm}"
        private_key_path = self.key_storage_path / key_filename
        public_key_path = self.key_storage_path / f"{key_filename}.pub"

        logger.info("=== Generating SSH Key Pair ===")
        logger.info(f"System:       {hostname_fqdn}")
        logger.info(f"Algorithm:    {self.algorithm}")
        logger.info(f"Comment:      {key_comment}")
        logger.info(f"Private Key:  {key_filename}")
        logger.info(f"Public Key:   {key_filename}.pub")

        # Check if key already exists
        if private_key_path.exists():
            logger.warning(f"Private key already exists: {private_key_path}")
            if not self.non_interactive:
                response = input("Overwrite existing key? (yes/no): ").strip().lower()
                if response != 'yes':
                    logger.info("Keeping existing key")
                    return private_key_path, public_key_path
            else:
                logger.info("Non-interactive mode: Skipping to preserve existing key")
                return private_key_path, public_key_path

        # Ensure .ssh directory exists
        self.key_storage_path.mkdir(mode=0o700, parents=True, exist_ok=True)

        # Generate key pair (no passphrase for automated server access)
        logger.info("Generating key pair (no passphrase for automated access)...")
        self._run_command([
            'ssh-keygen',
            '-t', self.algorithm,
            '-C', key_comment,
            '-f', str(private_key_path),
            '-N', '',  # No passphrase
            '-q'
        ])

        # Set permissions
        private_key_path.chmod(0o600)
        public_key_path.chmod(0o644)

        # Set ownership if running as root
        if os.geteuid() == 0:
            import pwd
            try:
                uid = pwd.getpwnam(self.user).pw_uid
                gid = pwd.getpwnam(self.user).pw_gid
                os.chown(private_key_path, uid, gid)
                os.chown(public_key_path, uid, gid)
            except Exception as e:
                logger.warning(f"Failed to set ownership: {e}")

        logger.info("✓ Key pair generated successfully")

        # Add to authorized_keys
        self._add_to_authorized_keys(public_key_path)

        # Get fingerprints
        fp_sha256 = self._get_key_fingerprint(public_key_path, 'sha256')
        fp_md5 = self._get_key_fingerprint(public_key_path, 'md5')

        logger.info("")
        logger.info("=== Key Generation Complete ===")
        logger.info(f"Private Key: {private_key_path}")
        logger.info(f"Public Key:  {public_key_path}")
        logger.info(f"Fingerprint (SHA256): {fp_sha256}")
        logger.info(f"Fingerprint (MD5):    {fp_md5}")

        # Send via Telegram if requested
        if send_private and self.telegram_bot_token and self.telegram_chat_id:
            self._send_key_via_telegram(
                private_key_path,
                public_key_path,
                hostname_fqdn,
                hostname_short,
                fp_sha256,
                fp_md5
            )
        elif send_private:
            logger.warning("Telegram not configured - private key NOT sent")
            logger.warning(f"Private key location: {private_key_path}")
            logger.warning(f"To retrieve: cat {private_key_path}")

        return private_key_path, public_key_path

    def generate_key_remote(
        self,
        key_owner: str,
        key_hostname: str,
        remote_connection: str,
        target_service: str = 'server'
    ) -> Tuple[Path, Path]:
        """
        Generate SSH key for remote deployment (client mode)
        
        Args:
            key_owner: Owner identifier (e.g., 'vb', username)
            key_hostname: Client hostname/context
            remote_connection: Remote connection string (user@host)
            target_service: Service/server identifier
            
        Returns:
            Tuple of (private_key_path, public_key_path)
        """
        # Parse remote connection
        if '@' in remote_connection:
            remote_user, remote_host = remote_connection.split('@', 1)
        else:
            remote_user = self.user
            remote_host = remote_connection

        # Naming convention:
        # - Private key filename: <service>-<user>-<algorithm>
        # - Public key comment: <owner>@<hostname>_<version>
        
        key_comment = f"{key_owner}@{key_hostname}_{self.version}"
        key_filename = f"{target_service}-{remote_user}-{self.algorithm}"
        private_key_path = self.key_storage_path / key_filename
        public_key_path = self.key_storage_path / f"{key_filename}.pub"

        logger.info("=== Generating SSH Key Pair ===")
        logger.info(f"Owner:           {key_owner}")
        logger.info(f"Context:         {key_hostname}")
        logger.info(f"Target Service:  {target_service}")
        logger.info(f"Target User:     {remote_user}")
        logger.info(f"Target Host:     {remote_host}")
        logger.info(f"Algorithm:       {self.algorithm}")
        logger.info(f"Private Key:     {key_filename}")
        logger.info(f"Public Comment:  {key_comment}")

        # Check if key already exists
        if private_key_path.exists():
            logger.warning(f"Private key already exists: {private_key_path}")
            if not self.non_interactive:
                response = input("Overwrite existing key? (yes/no): ").strip().lower()
                if response != 'yes':
                    logger.info("Aborting")
                    sys.exit(1)
            else:
                logger.error("Non-interactive mode: Cannot overwrite existing key")
                sys.exit(1)

        # Ensure .ssh directory exists
        self.key_storage_path.mkdir(mode=0o700, parents=True, exist_ok=True)

        # Generate key pair (with passphrase for client keys)
        logger.info("")
        logger.info("=== Generating Key Pair ===")
        
        if self.non_interactive:
            # No passphrase in non-interactive mode
            logger.info("Non-interactive mode: Generating key without passphrase")
            self._run_command([
                'ssh-keygen',
                '-t', self.algorithm,
                '-C', key_comment,
                '-f', str(private_key_path),
                '-N', ''
            ])
        else:
            # Interactive mode - ssh-keygen will prompt for passphrase
            logger.info("Enter passphrase when prompted (or press Enter for no passphrase)")
            subprocess.run([
                'ssh-keygen',
                '-t', self.algorithm,
                '-C', key_comment,
                '-f', str(private_key_path)
            ], check=True)

        # Set permissions
        private_key_path.chmod(0o600)
        public_key_path.chmod(0o644)

        logger.info("✓ Key pair generated successfully")

        # Add to ssh-agent
        self._add_to_ssh_agent(private_key_path)

        # Deploy to remote server
        logger.info("")
        logger.info("=== Deploying Public Key to Remote Server ===")
        logger.info(f"Target: {remote_connection}")
        logger.info("")
        logger.info("Note: You will be prompted for the password on first connection")

        try:
            self._run_command([
                'ssh-copy-id',
                '-i', str(private_key_path),
                remote_connection
            ], capture_output=False)
            logger.info("")
            logger.info("✓ Public key deployed successfully")
        except Exception as e:
            logger.error(f"Failed to deploy public key: {e}")
            sys.exit(1)

        # Test connection
        if not self.non_interactive:
            logger.info("")
            response = input("Test SSH connection now? (yes/no): ").strip().lower()
            if response == 'yes':
                self._test_ssh_connection(private_key_path, remote_connection)

        # Display SSH config
        self._display_ssh_config(
            target_service,
            remote_user,
            remote_host,
            remote_user,
            key_filename
        )

        return private_key_path, public_key_path

    def _add_to_authorized_keys(self, public_key_path: Path) -> None:
        """Add public key to authorized_keys file"""
        authorized_keys_path = self.key_storage_path / 'authorized_keys'

        # Create authorized_keys if it doesn't exist
        if not authorized_keys_path.exists():
            logger.info("Creating authorized_keys file")
            authorized_keys_path.touch(mode=0o600)
            with authorized_keys_path.open('w') as f:
                f.write(f"# Authorized SSH public keys for {self.user}\n")
                f.write(f"# Format: ssh-ALGORITHM KEY-DATA COMMENT\n")
                f.write(f"# Comment format: owner@hostname_version\n\n")
        
        # Backup existing file
        if authorized_keys_path.stat().st_size > 0:
            backup_path = authorized_keys_path.with_suffix(
                f'.backup-{datetime.now().strftime("%Y%m%d-%H%M%S")}'
            )
            import shutil
            shutil.copy2(authorized_keys_path, backup_path)
            logger.info(f"✓ Backup created: {backup_path}")

        # Add public key
        logger.info("Adding public key to authorized_keys...")
        with public_key_path.open('r') as src, authorized_keys_path.open('a') as dst:
            dst.write(src.read())
            if not src.read().endswith('\n'):
                dst.write('\n')

        # Ensure proper permissions
        authorized_keys_path.chmod(0o600)
        logger.info("✓ Public key added to authorized_keys")

    def _add_to_ssh_agent(self, private_key_path: Path) -> None:
        """Add private key to ssh-agent"""
        logger.info("")
        logger.info("=== Adding Key to SSH Agent ===")

        # Check if ssh-agent is running
        ssh_auth_sock = os.environ.get('SSH_AUTH_SOCK')
        if not ssh_auth_sock:
            logger.info("Starting ssh-agent...")
            try:
                result = self._run_command(['ssh-agent', '-s'])
                # Parse output to set environment variables
                for line in result.stdout.split('\n'):
                    if '=' in line and ';' in line:
                        var, val = line.split(';')[0].split('=', 1)
                        os.environ[var] = val
                        logger.debug(f"Set {var}={val}")
            except Exception as e:
                logger.warning(f"Failed to start ssh-agent: {e}")
                return
        else:
            logger.info(f"ssh-agent already running")

        # Add key to agent
        try:
            if self.non_interactive:
                # In non-interactive mode, key should have no passphrase
                self._run_command(['ssh-add', str(private_key_path)], capture_output=False)
            else:
                # Interactive mode - will prompt for passphrase if needed
                subprocess.run(['ssh-add', str(private_key_path)], check=True)
            logger.info("✓ Key added to ssh-agent")
        except Exception as e:
            logger.warning(f"Failed to add key to ssh-agent: {e}")

    def _test_ssh_connection(self, private_key_path: Path, remote_connection: str) -> None:
        """Test SSH connection"""
        logger.info("")
        logger.info("=== Testing SSH Connection ===")
        try:
            result = self._run_command([
                'ssh',
                '-i', str(private_key_path),
                remote_connection,
                'echo "✓ Connection successful!"'
            ], capture_output=False)
        except Exception as e:
            logger.error(f"Connection test failed: {e}")

    def _display_ssh_config(
        self,
        service: str,
        remote_user: str,
        remote_host: str,
        target_user: str,
        key_filename: str
    ) -> None:
        """Display SSH config suggestion"""
        logger.info("")
        logger.info("=== Setup Complete ===")
        logger.info("")
        logger.info("To use this key for SSH connections:")
        logger.info(f"  ssh -i {self.key_storage_path}/{key_filename} {remote_user}@{remote_host}")
        logger.info("")
        logger.info("Or add to your ~/.ssh/config:")
        logger.info(f"  Host {service}-{target_user}")
        logger.info(f"    HostName {remote_host}")
        logger.info(f"    User {remote_user}")
        logger.info(f"    IdentityFile {self.key_storage_path}/{key_filename}")
        logger.info("")
        logger.info("Then connect with:")
        logger.info(f"  ssh {service}-{target_user}")
        logger.info("")

    def _send_key_via_telegram(
        self,
        private_key_path: Path,
        public_key_path: Path,
        hostname_fqdn: str,
        hostname_short: str,
        fp_sha256: str,
        fp_md5: str
    ) -> None:
        """Send private key via Telegram"""
        logger.info("")
        logger.info("=== Sending Private Key via Telegram ===")

        key_filename = private_key_path.name

        message = f"""🔑 <b>SSH Server Access Key Generated</b>

<b>System:</b> {hostname_fqdn}
<b>User:</b> {self.user}
<b>Algorithm:</b> {self.algorithm}

<b>⚠️ SECURITY NOTICE</b>
This private key grants SSH access to this server.
Treat it as a password and store securely.

<b>Usage Instructions:</b>
1. Save the attached file securely
2. Set permissions: <code>chmod 600 {key_filename}</code>
3. Connect: <code>ssh -i {key_filename} {self.user}@{hostname_fqdn}</code>

<b>Fingerprints for Verification:</b>
SHA256: <code>{fp_sha256}</code>
MD5: <code>{fp_md5}</code>

<b>Add to ~/.ssh/config:</b>
<pre>Host {hostname_short}
    HostName {hostname_fqdn}
    User {self.user}
    IdentityFile ~/.ssh/{key_filename}</pre>

Then connect with: <code>ssh {hostname_short}</code>"""

        # Send message
        if self._send_telegram_message(message):
            logger.info("✓ Message sent")
        else:
            logger.warning("✗ Failed to send message")

        # Send private key file
        if self._send_telegram_file(private_key_path, f"🔐 Private Key: {key_filename}"):
            logger.info("✓ Private key sent")
            logger.info("")
            logger.info("⚠️  IMPORTANT: The private key has been sent via Telegram")
            logger.info("    Delete the message after saving the key securely")
            logger.info("    Consider rotating this key periodically")
        else:
            logger.warning("✗ Failed to send private key file")


def main():
    parser = argparse.ArgumentParser(
        description='SSH Key Generation and Deployment Tool',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate key for local server (bootstrap mode)
  %(prog)s --user root --send-private

  # Generate key for remote server
  %(prog)s --remote user@server.example.com --key-owner myname --key-hostname laptop

  # Non-interactive mode
  %(prog)s --user root --send-private --non-interactive

Environment Variables:
  TELEGRAM_BOT_TOKEN  - Telegram bot token
  TELEGRAM_CHAT_ID    - Telegram chat ID
  NONINTERACTIVE      - Skip prompts (yes/no)
        """
    )

    # Mode selection
    parser.add_argument(
        '--remote',
        metavar='USER@HOST',
        help='Deploy key to remote server (client mode). Format: user@hostname'
    )

    # Key configuration
    parser.add_argument(
        '--user',
        default=os.environ.get('USER', 'root'),
        help='Local user for .ssh directory (default: $USER or root)'
    )
    parser.add_argument(
        '--key-owner',
        help='Key owner identifier (for public key comment). Default: "client" for local, user for remote'
    )
    parser.add_argument(
        '--key-hostname',
        help='Hostname/context for key comment. Default: auto-detected'
    )
    parser.add_argument(
        '--service',
        default='server',
        help='Service/server identifier for key filename (default: server)'
    )
    parser.add_argument(
        '--algorithm',
        default='ed25519',
        choices=['ed25519', 'rsa', 'ecdsa'],
        help='Key algorithm (default: ed25519)'
    )
    parser.add_argument(
        '--key-path',
        help='Custom .ssh directory path (default: /home/USER/.ssh)'
    )

    # Telegram configuration
    parser.add_argument(
        '--send-private',
        action='store_true',
        help='Send private key via Telegram (local mode only)'
    )
    parser.add_argument(
        '--telegram-token',
        help='Telegram bot token (or use TELEGRAM_BOT_TOKEN env)'
    )
    parser.add_argument(
        '--telegram-chat-id',
        help='Telegram chat ID (or use TELEGRAM_CHAT_ID env)'
    )

    # Behavior
    parser.add_argument(
        '--non-interactive',
        action='store_true',
        help='Non-interactive mode (no prompts)'
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug logging'
    )

    args = parser.parse_args()

    # Configure logging
    if args.debug:
        logger.setLevel(logging.DEBUG)

    # Create key manager
    key_manager = SSHKeyManager(
        user=args.user,
        algorithm=args.algorithm,
        key_storage_path=args.key_path,
        telegram_bot_token=args.telegram_token,
        telegram_chat_id=args.telegram_chat_id,
        non_interactive=args.non_interactive
    )

    try:
        if args.remote:
            # Remote mode (client mode)
            hostname_fqdn, hostname_short = key_manager._get_system_hostname()
            
            key_owner = args.key_owner or args.user
            key_hostname = args.key_hostname or hostname_short
            
            key_manager.generate_key_remote(
                key_owner=key_owner,
                key_hostname=key_hostname,
                remote_connection=args.remote,
                target_service=args.service
            )
        else:
            # Local mode (server mode)
            key_owner = args.key_owner or 'client'
            hostname_fqdn, hostname_short = key_manager._get_system_hostname()
            key_hostname = args.key_hostname or hostname_fqdn
            
            key_manager.generate_key_local(
                key_owner=key_owner,
                key_hostname=key_hostname,
                send_private=args.send_private
            )

    except KeyboardInterrupt:
        logger.info("\nOperation cancelled by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Error: {e}")
        if args.debug:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
