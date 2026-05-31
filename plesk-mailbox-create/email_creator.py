#!/usr/bin/env python3
"""
Plesk Email Account Creator
Automates creation of email accounts from CSV data

"""

import csv
import secrets
import string
import tomllib
import requests
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class EmailAccount:
    """Email account data structure"""
    email_address: str
    domain: str
    aliases: List[str]
    contact_email: str
    account_type: str  # P, V, or team
    password: Optional[str] = None
    
    def __post_init__(self):
        if not self.password:
            self.password = self.generate_password()
    
    @staticmethod
    def generate_password(length: int = 20) -> str:
        """Generate random password [a-Z0-9]"""
        alphabet = string.ascii_letters + string.digits
        return ''.join(secrets.choice(alphabet) for _ in range(length))


class PleskEmailManager:
    """Manages Plesk email account creation via web interface"""
    
    def __init__(self, config_path: str = "config.toml"):
        self.config = self.load_config(config_path)
        self.session = requests.Session()
        self.base_url = self.config['plesk']['base_url']
        self.logged_in = False
        
    def load_config(self, config_path: str) -> dict:
        """Load configuration from TOML file"""
        with open(config_path, 'rb') as f:
            return tomllib.load(f)
    
    def login(self) -> bool:
        """Login to Plesk panel"""
        login_url = f"{self.base_url}/login_up.php"
        
        try:
            # Get login page first to obtain tokens
            response = self.session.get(login_url)
            response.raise_for_status()
            
            # Extract forgery protection token from page
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(response.text, 'html.parser')
            token_input = soup.find('input', {'name': 'forgery_protection_token'})
            token = token_input['value'] if token_input else ''
            
            # Perform login
            login_data = {
                'login_name': self.config['plesk']['username'],
                'passwd': self.config['plesk']['password'],
                'forgery_protection_token': token
            }
            
            response = self.session.post(login_url, data=login_data)
            response.raise_for_status()
            
            # Check if login successful
            if 'login_up.php' not in response.url:
                self.logged_in = True
                logger.info("Successfully logged in to Plesk")
                return True
            else:
                logger.error("Login failed - redirected back to login page")
                return False
                
        except Exception as e:
            logger.error(f"Login error: {e}")
            return False
    
    def create_email_account(self, account: EmailAccount) -> bool:
        """Create email account via Plesk interface"""
        if not self.logged_in:
            logger.error("Not logged in to Plesk")
            return False
        
        create_url = f"{self.base_url}/smb/email-address/create"
        
        try:
            # Get create form to obtain token
            response = self.session.get(create_url)
            response.raise_for_status()
            
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(response.text, 'html.parser')
            token_input = soup.find('input', {'name': 'forgery_protection_token'})
            token = token_input['value'] if token_input else ''
            
            # Extract email name and domain from full address
            email_name = account.email_address.split('@')[0]
            
            # Determine domain ID from config
            domain_id = self.config['domains'].get(account.domain)
            if not domain_id:
                logger.error(f"Domain {account.domain} not found in config")
                return False
            
            # Prepare form data
            form_data = {
                'general[generalSection][name]': email_name,
                'general[generalSection][domain]': domain_id,
                'general[generalSection][loginAsUser]': '1',
                'general[generalSection][password]': account.password,
                'general[generalSection][passwordConfirmation]': account.password,
                'general[generalSection][postbox]': '1',
                'general[generalSection][mboxQuotaValue]': 'default',
                'general[generalSection][mboxOutgoingMessages]': 'default',
                'forgery_protection_token': token,
                'send': ''
            }
            
            # Add external email if provided
            if account.contact_email:
                form_data['general[generalSection][externalEmail]'] = account.contact_email
            
            # Adjust based on account type
            if account.account_type == 'V':
                # Distribution list - no mailbox
                form_data['general[generalSection][postbox]'] = '0'
            
            # Submit form
            response = self.session.post(create_url, data=form_data)
            response.raise_for_status()
            
            # Check for success
            if 'email-address/list' in response.url or 'success' in response.text.lower():
                logger.info(f"Successfully created email: {account.email_address}")
                return True
            else:
                logger.error(f"Failed to create email: {account.email_address}")
                return False
                
        except Exception as e:
            logger.error(f"Error creating email {account.email_address}: {e}")
            return False
    
    def create_alias(self, main_email: str, alias: str) -> bool:
        """Create email alias"""
        # Aliases will be handled in separate step
        # This is a placeholder for the alias creation logic
        logger.info(f"Alias creation deferred: {alias} -> {main_email}")
        return True
    
    def send_notification_email(self, account: EmailAccount, success: bool):
        """Send notification email with account details"""
        if not account.contact_email:
            logger.warning(f"No contact email for {account.email_address}")
            return
        
        smtp_config = self.config['smtp']
        
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f"Email Account Created: {account.email_address}"
            msg['From'] = smtp_config['from_address']
            msg['To'] = account.contact_email
            
            # Create email body
            if success:
                body = f"""
Email Account Successfully Created

Account Details:
- Email Address: {account.email_address}
- Login: {account.email_address}
- Password: {account.password}
- Server: {self.config['email_server']['hostname']}
- IMAP Port: {self.config['email_server']['imap_port']}
- SMTP Port: {self.config['email_server']['smtp_port']}
- Encryption: {self.config['email_server']['encryption']}
- Account Type: {account.account_type}

"""
                if account.aliases:
                    body += f"Aliases (to be created):\n"
                    for alias in account.aliases:
                        body += f"  - {alias}\n"
                
                body += """
Please keep this information secure.

Best regards,
IT Administration
"""
            else:
                body = f"""
Email Account Creation Failed

There was an error creating the email account: {account.email_address}

Please contact IT support for assistance.
"""
            
            msg.attach(MIMEText(body, 'plain'))
            
            # Send email
            with smtplib.SMTP(smtp_config['host'], smtp_config['port']) as server:
                if smtp_config.get('use_tls'):
                    server.starttls()
                if smtp_config.get('username'):
                    server.login(smtp_config['username'], smtp_config['password'])
                server.send_message(msg)
            
            logger.info(f"Notification sent to {account.contact_email}")
            
        except Exception as e:
            logger.error(f"Failed to send notification: {e}")


class CSVProcessor:
    """Process CSV file with email account data"""
    
    def __init__(self, csv_path: str):
        self.csv_path = csv_path
    
    def read_accounts(self) -> List[EmailAccount]:
        """Read email accounts from CSV"""
        accounts = []
        
        with open(self.csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            
            for row in reader:
                # Skip empty rows
                if not row.get('Email-Adresse'):
                    continue
                
                # Extract data
                email_address = row['Email-Adresse'].strip()
                
                # Parse domain from email
                if '@' not in email_address:
                    logger.warning(f"Invalid email format: {email_address}")
                    continue
                
                domain = email_address.split('@')[1]
                
                # Parse aliases
                aliases = []
                if row.get('Alias'):
                    alias_text = row['Alias'].strip()
                    if alias_text:
                        aliases = [a.strip() for a in alias_text.split(',')]
                
                # Determine account type
                account_type = 'team'  # default
                if row.get('Typ'):
                    typ = row['Typ'].strip().upper()
                    if typ in ['P', 'V']:
                        account_type = typ
                
                account = EmailAccount(
                    email_address=email_address,
                    domain=domain,
                    aliases=aliases,
                    contact_email=row.get('Kontaktperson Email', '').strip(),
                    account_type=account_type
                )
                
                accounts.append(account)
        
        logger.info(f"Loaded {len(accounts)} accounts from CSV")
        return accounts


def main():
    """Main execution function"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Plesk Email Account Creator')
    parser.add_argument('csv_file', help='CSV file with email accounts')
    parser.add_argument('--config', default='config.toml', help='Configuration file')
    parser.add_argument('--dry-run', action='store_true', help='Dry run without creating accounts')
    
    args = parser.parse_args()
    
    # Load accounts from CSV
    processor = CSVProcessor(args.csv_file)
    accounts = processor.read_accounts()
    
    if args.dry_run:
        logger.info("DRY RUN MODE - No accounts will be created")
        for account in accounts:
            logger.info(f"Would create: {account.email_address} (type: {account.account_type})")
            if account.aliases:
                logger.info(f"  Aliases: {', '.join(account.aliases)}")
        return
    
    # Initialize Plesk manager
    manager = PleskEmailManager(args.config)
    
    # Login
    if not manager.login():
        logger.error("Failed to login to Plesk. Exiting.")
        return
    
    # Create accounts
    results = []
    for account in accounts:
        logger.info(f"Creating account: {account.email_address}")
        success = manager.create_email_account(account)
        results.append((account, success))
        
        # Send notification
        manager.send_notification_email(account, success)
    
    # Summary
    successful = sum(1 for _, success in results if success)
    logger.info(f"\nCompleted: {successful}/{len(results)} accounts created successfully")
    
    # List failed accounts
    failed = [(acc, succ) for acc, succ in results if not succ]
    if failed:
        logger.warning("\nFailed accounts:")
        for account, _ in failed:
            logger.warning(f"  - {account.email_address}")


if __name__ == '__main__':
    main()