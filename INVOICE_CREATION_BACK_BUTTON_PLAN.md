# Invoice Creation Back Button Implementation Plan

## Overview

Add an "Undo" button feature to the invoice creation flow that allows merchants to go back one step and re-enter their input.

## Requirements

Based on `invoice_back_button.txt`:

1. ✅ Display back button on EVERY prompt (except first step and preview)
2. ✅ Back button goes back only ONE step
3. ✅ Clear data for current step when going back
4. ✅ No back button on first step (STATE_COLLECT_MERCHANT_NAME)
5. ✅ No back button on preview step (STATE_READY - user can type 'cancel')
6. ✅ Error handling: Reset to IDLE if back navigation fails

## State Transition Flow

```
STATE_IDLE
  ↓
STATE_COLLECT_MERCHANT_NAME (NO back button)
  ↓
STATE_COLLECT_LINE_ITEMS (back → STATE_COLLECT_MERCHANT_NAME)
  ↓
STATE_COLLECT_VAT (back → STATE_COLLECT_LINE_ITEMS)
  ↓
STATE_COLLECT_DUE_DATE (back → STATE_COLLECT_VAT)
  ↓
STATE_COLLECT_PHONE (back → STATE_COLLECT_DUE_DATE)
  ↓
STATE_COLLECT_NAME (back → STATE_COLLECT_PHONE)
  ↓
STATE_COLLECT_MPESA_METHOD (back → STATE_COLLECT_NAME)
  ↓
STATE_COLLECT_PAYBILL_DETAILS (back → STATE_COLLECT_MPESA_METHOD)
  ↓
STATE_COLLECT_PAYBILL_ACCOUNT (back → STATE_COLLECT_PAYBILL_DETAILS)
  ↓
STATE_COLLECT_TILL_DETAILS (back → STATE_COLLECT_MPESA_METHOD)
  ↓
STATE_COLLECT_PHONE_DETAILS (back → STATE_COLLECT_MPESA_METHOD)
  ↓
STATE_ASK_SAVE_PAYMENT_METHOD (back → varies by payment method)
  ↓
STATE_READY (NO back button - can type 'cancel')
```

---

## Phase 1: State Transition Mapping ✅

**Task:** Define the state transition map for back navigation

**File:** `src/app/services/whatsapp.py`
**Location:** In `ConversationStateManager` class (around line 42)

**Implementation:**

```python
# In ConversationStateManager class
STATE_BACK_MAP = {
    STATE_COLLECT_LINE_ITEMS: STATE_COLLECT_MERCHANT_NAME,
    STATE_COLLECT_VAT: STATE_COLLECT_LINE_ITEMS,
    STATE_COLLECT_DUE_DATE: STATE_COLLECT_VAT,
    STATE_COLLECT_PHONE: STATE_COLLECT_DUE_DATE,
    STATE_COLLECT_NAME: STATE_COLLECT_PHONE,
    STATE_COLLECT_MPESA_METHOD: STATE_COLLECT_NAME,
    STATE_COLLECT_PAYBILL_DETAILS: STATE_COLLECT_MPESA_METHOD,
    STATE_COLLECT_PAYBILL_ACCOUNT: STATE_COLLECT_PAYBILL_DETAILS,
    STATE_COLLECT_TILL_DETAILS: STATE_COLLECT_MPESA_METHOD,
    STATE_COLLECT_PHONE_DETAILS: STATE_COLLECT_MPESA_METHOD,
    STATE_ASK_SAVE_PAYMENT_METHOD: None,  # Will be determined dynamically
}
```

**Notes:**

- `STATE_COLLECT_MERCHANT_NAME` has no previous state (first step)
- `STATE_READY` has no back button (user can type 'cancel')
- `STATE_ASK_SAVE_PAYMENT_METHOD` needs dynamic handling based on payment method

---

## Phase 2: Helper Method for Interactive Messages ✅

**Task:** Create `send_message_with_back_button()` method in WhatsAppService

**File:** `src/app/services/whatsapp.py`
**Location:** After `send_message()` method (around line 1200)

**Implementation:**

```python
async def send_message_with_back_button(
    self,
    recipient: str,
    message_text: str
) -> bool:
    """
    Send a WhatsApp interactive message with an Undo button.

    Uses 360Dialog API to send an interactive button message that allows
    merchants to go back one step in the invoice creation flow.

    Args:
        recipient: Phone number to send to (E.164 format without +)
        message_text: The prompt text to display

    Returns:
        True if sent successfully, False otherwise

    Example:
        >>> await service.send_message_with_back_button(
        ...     "254712345678",
        ...     "Please enter the customer's phone number:"
        ... )
    """
    url = f"{self.base_url}/messages"
    headers = {
        "D360-API-KEY": self.api_key,
        "Content-Type": "application/json",
    }

    payload = {
        "to": recipient,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {
                "text": message_text
            },
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": "undo",
                            "title": "Undo"
                        }
                    }
                ]
            }
        }
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, headers=headers, timeout=30.0)
            response.raise_for_status()

            logger.info(
                "Interactive message with back button sent successfully",
                extra={
                    "recipient": recipient,
                    "message_length": len(message_text)
                }
            )
            return True

    except httpx.HTTPStatusError as e:
        logger.error(
            "WhatsApp API error sending interactive message",
            extra={
                "status_code": e.response.status_code,
                "response": e.response.text,
                "recipient": recipient
            },
            exc_info=True
        )
        return False

    except Exception as e:
        logger.error(
            "Unexpected error sending interactive message",
            extra={
                "error": str(e),
                "recipient": recipient
            },
            exc_info=True
        )
        return False
```

---

## Phase 3: Update Flow Prompts ✅

**Task:** Modify all `return` statements in `handle_guided_flow()` to include back button flag

**File:** `src/app/services/whatsapp.py`
**Location:** Throughout `handle_guided_flow()` method

**States to Update (with back button):**

1. ✅ `STATE_COLLECT_LINE_ITEMS`
2. ✅ `STATE_COLLECT_VAT`
3. ✅ `STATE_COLLECT_DUE_DATE`
4. ✅ `STATE_COLLECT_PHONE`
5. ✅ `STATE_COLLECT_NAME`
6. ✅ `STATE_COLLECT_MPESA_METHOD`
7. ✅ `STATE_COLLECT_PAYBILL_DETAILS`
8. ✅ `STATE_COLLECT_PAYBILL_ACCOUNT`
9. ✅ `STATE_COLLECT_TILL_DETAILS`
10. ✅ `STATE_COLLECT_PHONE_DETAILS`
11. ✅ `STATE_ASK_SAVE_PAYMENT_METHOD`

**States WITHOUT back button:**

- ❌ `STATE_COLLECT_MERCHANT_NAME` (first step)
- ❌ `STATE_READY` (preview - can type 'cancel')
- ❌ `STATE_IDLE` (not in flow)

**Change Pattern:**

```python
# Before:
return {
    "response": "Some prompt text",
    "action": "some_action",
}

# After:
return {
    "response": "Some prompt text",
    "action": "some_action",
    "show_back_button": True,  # New flag
}
```

**Note:** Validation error responses should NOT have back button (they stay in same state)

---

## Phase 4: Webhook Handler for Undo Button ✅

**Task:** Add handler for interactive button clicks in `parse_incoming_message()`

**File:** `src/app/services/whatsapp.py`
**Location:** Around line 404 where interactive messages are parsed

**Current Code:**

```python
elif message_type == "interactive":
    # Handle button clicks
    interactive = message.get("interactive", {})
    interactive_type = interactive.get("type")

    if interactive_type == "button_reply":
        button_reply = interactive.get("button_reply", {})
        text = button_reply.get("title", "")
```

**Updated Code:**

```python
elif message_type == "interactive":
    # Handle button clicks
    interactive = message.get("interactive", {})
    interactive_type = interactive.get("type")

    if interactive_type == "button_reply":
        button_reply = interactive.get("button_reply", {})
        button_id = button_reply.get("id")

        # Check if it's the undo button
        if button_id == "undo":
            text = "undo"  # Special command
            logger.info(
                "Undo button clicked",
                extra={"sender": normalized_sender}
            )
        else:
            text = button_reply.get("title", "")
```

---

## Phase 5: Implement go_back() Method ✅

**Task:** Create method to handle back navigation with data clearing

**File:** `src/app/services/whatsapp.py`
**Location:** In `WhatsAppService` class (around line 1700)

**Implementation:**

```python
def go_back(self, user_id: str) -> Dict[str, Any]:
    """
    Handle back navigation in invoice creation flow.

    Navigates to the previous step, clears current step data,
    and returns the prompt for the previous step.

    Args:
        user_id: The merchant's user ID (phone number)

    Returns:
        Dictionary with response, action, and show_back_button flag

    Example:
        >>> result = service.go_back("254712345678")
        >>> result["response"]
        "Please enter your line items..."
    """
    state_info = self.state_manager.get_state(user_id)
    current_state = state_info["state"]
    data = state_info["data"]

    logger.info(
        "Back navigation requested",
        extra={
            "user_id": user_id,
            "current_state": current_state
        }
    )

    # Determine previous state
    if current_state == self.state_manager.STATE_ASK_SAVE_PAYMENT_METHOD:
        # Dynamic back based on payment method
        mpesa_method = data.get("mpesa_method")
        if mpesa_method == "PAYBILL":
            previous_state = self.state_manager.STATE_COLLECT_PAYBILL_ACCOUNT
        elif mpesa_method == "TILL":
            previous_state = self.state_manager.STATE_COLLECT_TILL_DETAILS
        elif mpesa_method == "PHONE":
            previous_state = self.state_manager.STATE_COLLECT_PHONE_DETAILS
        else:
            logger.error(
                "Invalid payment method for back navigation",
                extra={
                    "user_id": user_id,
                    "mpesa_method": mpesa_method
                }
            )
            return self._handle_back_error(user_id)
    else:
        previous_state = self.state_manager.STATE_BACK_MAP.get(current_state)

    if not previous_state:
        logger.warning(
            "No previous state found for back navigation",
            extra={
                "user_id": user_id,
                "current_state": current_state
            }
        )
        return self._handle_back_error(user_id)

    # Clear data for current step
    data_to_clear = self._get_data_keys_for_state(current_state)
    for key in data_to_clear:
        data.pop(key, None)

    logger.debug(
        "Cleared data for current state",
        extra={
            "user_id": user_id,
            "current_state": current_state,
            "keys_cleared": data_to_clear
        }
    )

    # Set previous state
    self.state_manager.set_state(user_id, previous_state, data)

    # Return the prompt for previous state
    return self._get_prompt_for_state(previous_state, data, user_id)


def _get_data_keys_for_state(self, state: str) -> list:
    """
    Get the data keys that should be cleared for a given state.

    Args:
        state: The state to get data keys for

    Returns:
        List of data keys to clear
    """
    state_data_map = {
        self.state_manager.STATE_COLLECT_MERCHANT_NAME: ["merchant_name"],
        self.state_manager.STATE_COLLECT_LINE_ITEMS: ["line_items"],
        self.state_manager.STATE_COLLECT_VAT: ["include_vat"],
        self.state_manager.STATE_COLLECT_DUE_DATE: ["due_date"],
        self.state_manager.STATE_COLLECT_PHONE: ["phone"],
        self.state_manager.STATE_COLLECT_NAME: ["name"],
        self.state_manager.STATE_COLLECT_MPESA_METHOD: ["mpesa_method"],
        self.state_manager.STATE_COLLECT_PAYBILL_DETAILS: [
            "mpesa_paybill_number",
            "saved_paybill_methods"
        ],
        self.state_manager.STATE_COLLECT_PAYBILL_ACCOUNT: ["mpesa_account_number"],
        self.state_manager.STATE_COLLECT_TILL_DETAILS: [
            "mpesa_till_number",
            "saved_till_methods"
        ],
        self.state_manager.STATE_COLLECT_PHONE_DETAILS: [
            "mpesa_phone_number",
            "saved_phone_methods"
        ],
        self.state_manager.STATE_ASK_SAVE_PAYMENT_METHOD: ["save_payment_method"],
    }

    return state_data_map.get(state, [])


def _get_prompt_for_state(
    self,
    state: str,
    data: Dict[str, Any],
    user_id: str
) -> Dict[str, Any]:
    """
    Get the prompt message for a given state.

    Args:
        state: The state to get prompt for
        data: Current conversation data
        user_id: The merchant's user ID

    Returns:
        Dictionary with response, action, and show_back_button flag
    """
    # This will return the same prompts as in handle_guided_flow()
    # but without processing any input

    # Import here to avoid circular dependency
    from ..db import get_supabase

    if state == self.state_manager.STATE_COLLECT_MERCHANT_NAME:
        return {
            "response": "Let's create an invoice!\n\nFirst, what is your business/merchant name? (2-100 characters)",
            "action": "back_to_merchant_name",
            "show_back_button": False,
        }

    elif state == self.state_manager.STATE_COLLECT_LINE_ITEMS:
        return {
            "response": (
                "Please enter your line items in the following format:\n\n"
                "Item - Price - Quantity\n\n"
                "Example:\n"
                "Laptop Repair - 5000 - 1\n"
                "Screen Protector - 500 - 2\n\n"
                "You can enter multiple items, one per line."
            ),
            "action": "back_to_line_items",
            "show_back_button": True,
        }

    # ... (continue for all states)

    else:
        logger.error(
            "Unknown state in _get_prompt_for_state",
            extra={"state": state, "user_id": user_id}
        )
        return self._handle_back_error(user_id)


def _handle_back_error(self, user_id: str) -> Dict[str, Any]:
    """
    Handle error when back navigation fails.

    Args:
        user_id: The merchant's user ID

    Returns:
        Error response with instructions to start over
    """
    self.state_manager.clear_state(user_id)
    logger.warning(
        "Back navigation failed, clearing state",
        extra={"user_id": user_id}
    )

    return {
        "response": (
            "Sorry, something went wrong with the back navigation. "
            "Please start over by sending 'invoice'."
        ),
        "action": "back_error",
        "show_back_button": False,
    }
```

---

## Phase 6: Router Integration ✅

**Task:** Update webhook router to handle undo command and send messages with back button

**File:** `src/app/routers/whatsapp.py`
**Location:** In the webhook handler (around line 636)

**Changes:**

1. **Handle "undo" command** (before processing guided flow):

```python
# Check for undo command
if is_in_flow and message_text.lower() == "undo":
    flow_result = whatsapp_service.go_back(sender)
    response_text = flow_result["response"]
    show_back_button = flow_result.get("show_back_button", False)

    logger.info(
        "Undo command processed",
        extra={
            "sender": sender,
            "action": flow_result.get("action")
        }
    )
```

2. **Send messages with back button** (in response sending section):

```python
# Send response to user
if response_text:
    show_back_button = flow_result.get("show_back_button", False)

    if show_back_button:
        await whatsapp_service.send_message_with_back_button(sender, response_text)
    else:
        await whatsapp_service.send_message(sender, response_text)
```

---

## Phase 7: Error Handling ✅

**Task:** Ensure robust error handling throughout

**Checklist:**

- ✅ Handle invalid state transitions gracefully
- ✅ Log all back navigation attempts
- ✅ Clear state and prompt restart on error
- ✅ Handle missing payment method data
- ✅ Validate state exists in STATE_BACK_MAP

---

## Phase 8: Testing & Validation ✅

**Tasks:**

1. **Linter Check:**

```bash
ruff check src/app/
```

2. **Test Scenarios:**
   - ✅ Back button appears on all correct states
   - ✅ No back button on first step and preview
   - ✅ Back navigation clears current data only
   - ✅ Payment method back navigation works correctly
   - ✅ Error handling resets to IDLE properly
   - ✅ Undo button click is properly detected

3. **Manual Testing Flow:**

```
1. Start invoice flow
2. Enter merchant name (no back button)
3. Enter line items (back button appears)
4. Click Undo → should return to merchant name prompt
5. Enter merchant name again
6. Continue through all states testing back button at each step
7. Test error case by manipulating state
```

---

## Summary of Changes

### Files Modified:

1. **`src/app/services/whatsapp.py`:**
   - Add `STATE_BACK_MAP` to `ConversationStateManager` class
   - Add `send_message_with_back_button()` method
   - Add `go_back()` method
   - Add `_handle_back_error()` helper method
   - Add `_get_data_keys_for_state()` helper method
   - Add `_get_prompt_for_state()` helper method
   - Update `parse_incoming_message()` to handle "undo" button
   - Add `show_back_button: True` flag to all relevant state returns

2. **`src/app/routers/whatsapp.py`:**
   - Add check for "undo" command before guided flow processing
   - Add conditional send logic based on `show_back_button` flag

### No New Dependencies

Uses existing WhatsApp API capabilities via 360Dialog.

---

## WhatsApp API Reference

**Interactive Button Message Format:**

```json
{
  "to": "<customer-msisdn>",
  "type": "interactive",
  "interactive": {
    "type": "button",
    "body": {
      "text": "<prompt-text>"
    },
    "action": {
      "buttons": [
        {
          "type": "reply",
          "reply": {
            "id": "undo",
            "title": "Undo"
          }
        }
      ]
    }
  }
}
```

**Button Click Webhook:**

```json
{
  "interactive": {
    "type": "button_reply",
    "button_reply": {
      "id": "undo",
      "title": "Undo"
    }
  }
}
```

---

## Implementation Order

Follow these phases in order:

1. ✅ Phase 1: State Transition Mapping
2. ✅ Phase 2: Helper Method
3. ✅ Phase 3: Update Flow Prompts
4. ✅ Phase 4: Webhook Handler
5. ✅ Phase 5: go_back() Method
6. ✅ Phase 6: Router Integration
7. ✅ Phase 7: Error Handling
8. ✅ Phase 8: Testing & Validation
