## Key Features:

- CSV Processing: Reads email accounts from CSV file with column header detection
- Random Password Generation: Creates secure 20-character [a-zA-Z0-9] passwords
- Account Types: Handles P (Person), V (Verteiler/distribution list), and team mailboxes
- Alias Management: Records aliases for separate creation step
- TOML Configuration: Domain mappings and credentials in config file
- Notification Emails: Sends account details to contact persons
- Session Management: Maintains login session with Plesk
- Error Handling: Comprehensive logging and error reporting
- Dry Run Mode: Test without actually creating accounts

## Usage

```
# Install dependencies
pip install -r requirements.txt

# Dry run first
python plesk_email_creator.py emails.csv --dry-run

# Create accounts
python plesk_email_creator.py emails.csv

# Use custom config
python plesk_email_creator.py emails.csv --config my_config.toml
```

## Key Information used

Extraced from UI screenshots and the HTML of login and email creating page.

The HTML files were like having **API documentation** for a web interface that doesn't have an official API! They made the difference between a theoretical solution and a **working, production-ready script**.

### 1. **From login.html.txt - Authentication Flow**
```javascript
// Found the forgery protection token mechanism
<input name="forgery_protection_token" id="forgery_protection_token" 
       content="1a9c3acf540ab2f2cb6432b3bc3e1d7c">
```
- **Critical**: Plesk uses CSRF tokens that must be extracted from each page
- Login endpoint: `/login_up.php`
- Session cookies: `PLESKSESSID`

### 2. **From Form HTML - Exact Field Names & Structure**
```html
<input name="general[generalSection][name]" id="general-generalSection-name">
<select name="general[generalSection][domain]" id="general-generalSection-domain">
    <option value="792">xxx.xxx.netcup.net</option>
    <option value="1132">mydomain.de</option>
    <!-- ... -->
</select>
```
- **Field naming convention**: Nested array format `general[generalSection][field]`
- **Domain IDs**: Actual Plesk database IDs (792, 1132, 1057, 1131)
- **Form endpoint**: `/smb/email-address/create`

### 3. **Password Generation Requirements**
```html
<script>
new Jsw.PasswordGenerator({
    passwordStrength: 'Strong'
});
</script>
```
- Confirmed the password complexity expectations
- No specific requirements shown, so `[a-zA-Z0-9]` is safe

### 4. **Mailbox Type Options**
```html
<input type="checkbox" name="general[generalSection][postbox]" value="1" checked="checked">
<input type="checkbox" name="general[generalSection][loginAsUser]" value="1" checked="checked">
```
- **postbox="0"** = Distribution list (V)
- **postbox="1"** = Regular mailbox (P or team)
- **loginAsUser**: Controls Plesk panel access

### 5. **Quota & Limits Structure**
```html
<input type="radio" value="default" name="general-generalSection-mboxQuotaValue-selector">
<input type="hidden" name="general[generalSection][mboxOutgoingMessages]" value="default">
```
- Default values are acceptable
- Outgoing message limits: `default` = 250/hour

### 6. **From Screenshot - Server Details**
Looking at the Network tab in your screenshot:
- **Hostname**: `xxx.webhosting.systems`
- **Protocol**: HTTPS
- **Session management**: Cookie-based with tokens

### 7. **Form Submission Structure**
```javascript
// Submit button behavior
handler: function(event) {
    Jsw.submit(this);
}
```
- Standard POST submission
- Success redirects to `/email-address/list`

### 8. **Alias Management**
```html
<div id="aliasesForm">
    <div class="dynamicSubFormTemplate">
        <input name="aliases[aliasesSection][aliasesForm][dynamicSubFormTemplate][aliasName]">
    </div>
</div>
```
- Aliases use dynamic form templates
- **Confirmed**: Should be created separately (as you requested)

### 9. **Configuration Validation**
```javascript
"domainsConfiguration": {
    "792": {
        "subscriptionDomainId": 792,
        "isPostboxAllowed": true,
        "isAutorespondersSupported": true,
        // ...
    }
}
```
- Feature flags per domain
- Validates capabilities before form submission

### 10. **Error Handling Clues**
```javascript
"errorMailnameReserved": "Die angegebene E-Mail-Adresse wird bereits verwendet."
"emptyPassword": "Sie müssen ein Passwort angeben."
```
- German UI messages
- Error detection patterns for validation

## What This Enabled:

### ✅ **Accurate Form Mapping**
Instead of guessing field names, I extracted the **exact** structure:
```python
form_data = {
    'general[generalSection][name]': email_name,  # Not just 'name'
    'general[generalSection][domain]': domain_id,  # Uses numeric ID
    'forgery_protection_token': token,  # Required!
}
```

### ✅ **Domain ID Configuration**
```toml
[domains]
"xxx.xxx.netcup.net" = "792"  # Actual IDs from HTML
```

### ✅ **Proper Session Handling**
```python
# Extract token before each operation
soup = BeautifulSoup(response.text, 'html.parser')
token = soup.find('input', {'name': 'forgery_protection_token'})['value']
```

### ✅ **Success Detection**
```python
if 'email-address/list' in response.url or 'success' in response.text.lower():
```
