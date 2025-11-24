# WABA to 360 Dialog Migration Guide

## Overview

This document outlines the migration from direct WhatsApp Business API (WABA) environment variables to 360 Dialog-specific configuration. 360 Dialog acts as a Business Solution Provider (BSP) for WhatsApp, providing a simplified API proxy that handles phone number management internally.

## Environment Variables Mapping

### Removed WABA Variables

| WABA Variable | Used In | Purpose | 360 Dialog Replacement |
|--------------|---------|---------|------------------------|
| `WABA_TOKEN` | `services/whatsapp.py` (lines 208, 555, 648) | Graph API authentication token | `D360_API_KEY` - 360 Dialog uses API key authentication instead of bearer tokens |
| `WABA_PHONE_ID` | `services/whatsapp.py` (lines 209, 217, 553, 646) | WhatsApp phone number ID for Graph API endpoint construction | Not needed - 360 Dialog manages phone number mapping internally |
| `WABA_VERIFY_TOKEN` | `routers/whatsapp.py` (lines 56, 125) | Webhook verification token (standard WhatsApp webhook verification) | `WEBHOOK_VERIFY_TOKEN` - Renamed for clarity (same purpose) |
| `WABA_APP_SECRET` | `routers/whatsapp.py` (line 49) | HMAC signature validation for webhook security | TODO: 360 Dialog uses different signature method - to be implemented |

### New/Updated Variables

| Variable | Type | Purpose | Example |
|----------|------|---------|---------|
| `D360_API_KEY` | Required | 360 Dialog API key for authentication | From 360 Dialog Partner Portal |
| `D360_WEBHOOK_BASE_URL` | Optional | 360 Dialog API base URL | `https://waba-v2.360dialog.io` (default) |
| `WEBHOOK_VERIFY_TOKEN` | Required | Webhook verification token (renamed from `WABA_VERIFY_TOKEN`) | Random string for webhook verification |

## Key Differences: WABA vs 360 Dialog

### 1. Authentication Method

**WABA (Direct Meta Graph API):**
```python
headers = {
    "Authorization": f"Bearer {waba_token}",
    "Content-Type": "application/json"
}
```

**360 Dialog:**
```python
headers = {
    "D360-API-KEY": f"{d360_api_key}",
    "Content-Type": "application/json"
}
```

### 2. API Endpoint Structure

**WABA (Direct Meta Graph API):**
```python
# Phone ID is part of the URL path
url = f"https://graph.facebook.com/v21.0/{phone_id}/messages"
```

**360 Dialog:**
```python
# Phone ID is managed internally by 360 Dialog
url = f"{d360_webhook_base_url}/messages"
```

### 3. Phone Number Management

**WABA:** Requires explicit phone number ID in the API endpoint URL.

**360 Dialog:** Phone number is linked to your API key in the 360 Dialog Partner Portal. The API automatically routes messages through the correct phone number based on your API key.

### 4. Webhook Signature Validation

**WABA:** Uses HMAC SHA256 with `app_secret`:
```python
import hmac
import hashlib

expected_signature = hmac.new(
    waba_app_secret.encode('utf-8'),
    payload_bytes,
    hashlib.sha256
).hexdigest()
```

**360 Dialog:** Different signature method (to be implemented). See: https://docs.360dialog.com/webhooks/signature-verification

## Code Changes Required

### 1. Configuration (`src/app/config.py`)

**Before (WABA):**
```python
class Settings(BaseSettings):
    # WhatsApp Business API Configuration
    waba_token: str
    waba_phone_id: str
    waba_verify_token: str
    # waba_app_secret would be here for production
```

**After (360 Dialog):**
```python
class Settings(BaseSettings):
    # WhatsApp Business API Configuration (via 360 Dialog)
    d360_api_key: str
    d360_webhook_base_url: str = "https://waba-v2.360dialog.io"
    webhook_verify_token: str  # Renamed from waba_verify_token
```

### 2. WhatsApp Service (`src/app/services/whatsapp.py`)

#### Initialization (lines 206-219)

**Before (WABA):**
```python
def __init__(self) -> None:
    """Initialize the WhatsApp service with configuration from settings."""
    self.waba_token = settings.waba_token
    self.waba_phone_id = settings.waba_phone_id
    self.base_url = "https://graph.facebook.com/v21.0"
    self.state_manager = ConversationStateManager

    logger.info(
        "WhatsAppService initialized",
        extra={
            "base_url": self.base_url,
            "phone_id": self.waba_phone_id[:5] + "..." if self.waba_phone_id else None,
        },
    )
```

**After (360 Dialog):**
```python
def __init__(self) -> None:
    """Initialize the WhatsApp service with configuration from settings."""
    # 360 Dialog uses API key authentication instead of bearer tokens
    self.api_key = settings.d360_api_key
    # Phone ID not needed - 360 Dialog manages phone number mapping internally
    # based on the API key configuration in the Partner Portal
    self.base_url = settings.d360_webhook_base_url
    self.state_manager = ConversationStateManager

    logger.info(
        "WhatsAppService initialized",
        extra={
            "base_url": self.base_url,
            "provider": "360dialog",
        },
    )
```

#### Message Sending (lines 527-608)

**Before (WABA):**
```python
url = f"{self.base_url}/{self.waba_phone_id}/messages"
headers = {
    "Authorization": f"Bearer {self.waba_token}",
    "Content-Type": "application/json",
}
```

**After (360 Dialog):**
```python
url = f"{self.base_url}/messages"
headers = {
    "D360-API-KEY": self.api_key,
    "Content-Type": "application/json",
}
```

#### Invoice Sending (lines 610-830)

**Before (WABA):**
```python
url = f"{self.base_url}/{self.waba_phone_id}/messages"
headers = {
    "Authorization": f"Bearer {self.waba_token}",
    "Content-Type": "application/json",
}
```

**After (360 Dialog):**
```python
url = f"{self.base_url}/messages"
headers = {
    "D360-API-KEY": self.api_key,
    "Content-Type": "application/json",
}
```

### 3. Webhook Router (`src/app/routers/whatsapp.py`)

#### Webhook Signature Validation (lines 29-63)

**Before (WABA):**
```python
def validate_webhook_signature(payload: dict[str, Any], signature: str) -> bool:
    """
    Validate WhatsApp webhook signature for production security.

    TODO: Implement HMAC signature validation in production.
    Example implementation for production:
        import hmac
        import hashlib
        payload_bytes = json.dumps(payload, separators=(',', ':')).encode('utf-8')
        expected_signature = hmac.new(
            settings.waba_app_secret.encode('utf-8'),
            payload_bytes,
            hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(f"sha256={expected_signature}", signature)
    """
    if not settings.waba_verify_token:
        logger.warning(
            "Webhook signature validation is disabled in MVP - "
            "WABA_APP_SECRET not configured. Enable for production!",
            extra={"has_signature": bool(signature)},
        )
    return True  # Always allow in MVP
```

**After (360 Dialog):**
```python
def validate_webhook_signature(payload: dict[str, Any], signature: str) -> bool:
    """
    Validate WhatsApp webhook signature for production security.

    TODO: Implement 360 Dialog webhook signature verification.
    360 Dialog uses a different signature method than Meta WABA.
    See: https://docs.360dialog.com/webhooks/signature-verification

    This is a placeholder for MVP - production should validate webhooks
    using 360 Dialog's signature verification method.
    """
    logger.warning(
        "Webhook signature validation is disabled in MVP. "
        "Implement 360 Dialog signature verification for production!",
        extra={"has_signature": bool(signature)},
    )
    return True  # Always allow in MVP
```

#### Webhook Verification (lines 82-142)

**Before (WABA):**
```python
# Validate verify token
if hub_verify_token != settings.waba_verify_token:
    logger.warning(
        "Webhook verification failed: invalid verify token",
        extra={"provided_token_length": len(hub_verify_token)},
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Invalid verify token",
    )
```

**After (360 Dialog):**
```python
# Validate verify token (standard WhatsApp webhook verification)
if hub_verify_token != settings.webhook_verify_token:
    logger.warning(
        "Webhook verification failed: invalid verify token",
        extra={"provided_token_length": len(hub_verify_token)},
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Invalid verify token",
    )
```

### 4. Environment Configuration (`.env.example`)

**Before (WABA):**
```bash
# WhatsApp Business API Configuration
WABA_TOKEN=your_whatsapp_token_here
WABA_PHONE_ID=your_phone_id_here
WABA_VERIFY_TOKEN=your_webhook_verification_token_here
```

**After (360 Dialog):**
```bash
# 360 Dialog API Configuration - WhatsApp Business API
# Get these values from 360 Dialog Partner Portal
# Documentation: https://docs.360dialog.com/

# 360 Dialog API Key from Partner Portal
D360_API_KEY=your_360dialog_api_key_here

# 360 Dialog Webhook Base URL (WABA v2 endpoint)
D360_WEBHOOK_BASE_URL=https://waba-v2.360dialog.io

# Webhook verification token (create a random string)
# Used for standard WhatsApp webhook verification
WEBHOOK_VERIFY_TOKEN=your_webhook_verification_token_here

# Note: Configure webhook in 360 Dialog Partner Portal
# Webhook URL: https://<your-domain>.fly.dev/whatsapp/webhook
```

## Implementation Steps

### Step 1: Update Environment Variables

1. **Get your 360 Dialog API Key:**
   - Log in to the 360 Dialog Partner Portal
   - Navigate to your WhatsApp Business Account
   - Copy your API key

2. **Update your `.env` file:**
   ```bash
   # Remove old WABA variables
   # WABA_TOKEN=...
   # WABA_PHONE_ID=...
   # WABA_VERIFY_TOKEN=...

   # Add new 360 Dialog variables
   D360_API_KEY=your_360dialog_api_key_here
   D360_WEBHOOK_BASE_URL=https://waba-v2.360dialog.io
   WEBHOOK_VERIFY_TOKEN=your_random_verification_token_here
   ```

3. **Generate a webhook verification token:**
   ```bash
   # Generate a random token for webhook verification
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

### Step 2: Configure Webhook in 360 Dialog

1. **Set your webhook URL in 360 Dialog Partner Portal:**
   ```bash
   curl --request POST \
     --url https://waba-v2.360dialog.io/v1/configs/webhook \
     --header 'Content-Type: application/json' \
     --header 'D360-Api-Key: YOUR_API_KEY' \
     --data '{"url": "https://your-domain.fly.dev/whatsapp/webhook"}'
   ```

2. **Verify webhook is configured:**
   ```bash
   curl --request GET \
     --url https://waba-v2.360dialog.io/v1/configs/webhook \
     --header 'D360-Api-Key: YOUR_API_KEY'
   ```

### Step 3: Test the Integration

1. **Test message sending:**
   - Send a test message through the WhatsApp service
   - Verify it arrives at the recipient's phone

2. **Test webhook receiving:**
   - Send a message to your WhatsApp Business number
   - Verify the webhook receives the message
   - Check logs for proper parsing

3. **Test invoice flow:**
   - Create an invoice using the bot
   - Verify customer receives invoice with payment button
   - Test payment button click handling

### Step 4: Update Deployment Configuration

Update your `fly.toml` secrets:
```bash
# Remove old secrets
fly secrets unset WABA_TOKEN WABA_PHONE_ID WABA_VERIFY_TOKEN

# Set new secrets
fly secrets set \
  D360_API_KEY="your_360dialog_api_key" \
  D360_WEBHOOK_BASE_URL="https://waba-v2.360dialog.io" \
  WEBHOOK_VERIFY_TOKEN="your_verification_token"
```

## API Compatibility

### Message Payload Format

Good news! The message payload format is **100% compatible** between WABA and 360 Dialog. 360 Dialog acts as a proxy and uses the same WhatsApp Cloud API message structure.

**Text Message (unchanged):**
```json
{
  "messaging_product": "whatsapp",
  "recipient_type": "individual",
  "to": "254712345678",
  "type": "text",
  "text": {
    "body": "Hello, customer!"
  }
}
```

**Interactive Button Message (unchanged):**
```json
{
  "messaging_product": "whatsapp",
  "recipient_type": "individual",
  "to": "254712345678",
  "type": "interactive",
  "interactive": {
    "type": "button",
    "body": {
      "text": "Invoice INV-123\nAmount: KES 1000 | Service description"
    },
    "action": {
      "buttons": [
        {
          "type": "reply",
          "reply": {
            "id": "pay_INV-123",
            "title": "Pay with M-PESA"
          }
        }
      ]
    }
  }
}
```

### Webhook Payload Format

Webhook payloads are also **100% compatible**. 360 Dialog forwards the standard WhatsApp Cloud API webhook structure.

## Troubleshooting

### Issue: "Invalid API Key" Error

**Symptom:** API requests return 401 Unauthorized

**Solution:**
1. Verify your API key is correct in `.env`
2. Check the API key hasn't expired in 360 Dialog Partner Portal
3. Ensure you're using `D360-API-KEY` header (not `Authorization`)

### Issue: Messages Not Sending

**Symptom:** API returns 404 Not Found

**Solution:**
1. Verify `D360_WEBHOOK_BASE_URL` is set correctly
2. Check your phone number is properly linked to the API key in 360 Dialog
3. Ensure phone numbers are in E.164 format (e.g., `2547XXXXXXXX`)

### Issue: Webhooks Not Received

**Symptom:** No webhook events arriving at your endpoint

**Solution:**
1. Verify webhook URL is configured in 360 Dialog Partner Portal
2. Check webhook URL is publicly accessible (use ngrok for local testing)
3. Ensure webhook verification token matches
4. Check Fly.io logs for incoming webhook requests

### Issue: Webhook Verification Fails

**Symptom:** Meta returns verification error when setting up webhook

**Solution:**
1. Ensure `WEBHOOK_VERIFY_TOKEN` environment variable is set
2. Verify your application is deployed and accessible
3. Check the verification endpoint (`GET /whatsapp/webhook`) is working
4. Use the exact same token in both your app and 360 Dialog configuration

## Additional Resources

- [360 Dialog Documentation](https://docs.360dialog.com/)
- [360 Dialog Partner Portal](https://hub.360dialog.com/)
- [WhatsApp Cloud API Documentation](https://developers.facebook.com/docs/whatsapp/cloud-api)
- [360 Dialog API Reference](https://docs.360dialog.com/docs/waba-messaging)
- [Webhook Configuration Guide](https://docs.360dialog.com/docs/waba-messaging/webhook)

## Migration Checklist

- [ ] Update `src/app/config.py` - Remove WABA variables
- [ ] Update `src/app/services/whatsapp.py` - Use 360 Dialog authentication
- [ ] Update `src/app/routers/whatsapp.py` - Update webhook verification
- [ ] Update `.env.example` - Document new variables
- [ ] Update `.env` - Add 360 Dialog credentials
- [ ] Configure webhook in 360 Dialog Partner Portal
- [ ] Test message sending
- [ ] Test webhook receiving
- [ ] Test full invoice flow
- [ ] Update Fly.io secrets
- [ ] Deploy to production
- [ ] Run end-to-end tests
- [ ] Monitor logs for issues

## Security Notes

1. **API Key Protection:**
   - Never commit `D360_API_KEY` to version control
   - Rotate API keys regularly
   - Use different keys for development/staging/production

2. **Webhook Security:**
   - Implement 360 Dialog webhook signature verification for production
   - Use HTTPS for webhook endpoints
   - Validate all incoming webhook payloads

3. **Token Management:**
   - Keep `WEBHOOK_VERIFY_TOKEN` secret
   - Use cryptographically random tokens
   - Update verification token if compromised